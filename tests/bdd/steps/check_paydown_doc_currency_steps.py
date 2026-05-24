"""Step definitions for check_paydown_doc_currency.feature.

Asserts the KFEAT-018 release-time gate behaves correctly:

- Snapshot within 7 days of the most recent release tag → exit 0.
- Snapshot >7 days old without an extension comment → exit 1 with the
  F21-shape affordance.
- Snapshot >7 days old WITH a forward-dated
  ``<!-- expected-out-of-date-until: YYYY-MM-DD -->`` comment → exit 0.

Tests construct an isolated repo-shaped tmp directory and invoke
``check_paydown_doc_currency.check_currency(repo_root)`` directly so
the scenarios are hermetic — no dependency on the host repo's tag
history.

F1-clean: no monkeypatching (the check accepts ``repo_root`` and an
optional ``today`` as injection seams). F13-clean: scenarios reference
operator concepts (snapshot, release tag, extension comment).

The fixture is named ``paydown_state`` rather than ``state`` so it
does not collide with sibling step modules that define a ``state``
dict fixture (pytest-bdd shares fixtures by name across all loaded
step modules; same-name collisions silently win the most-recently-
imported binding).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import pytest
from pytest_bdd import given, then, when

from scripts.checks.check_paydown_doc_currency import check_currency

pytestmark = pytest.mark.bdd


# ---------------------------------------------------------------------------
# Per-scenario state container
# ---------------------------------------------------------------------------


@dataclass
class _State:
    """Per-scenario state — built up by Given steps, consumed by When/Then."""

    repo_root: Path | None = None
    tag_date: date | None = None
    snapshot_date: date | None = None
    extension_until: date | None = None
    exit_code: int | None = None
    output_lines: list[str] = field(default_factory=list)


@pytest.fixture
def paydown_state(tmp_path: Path) -> _State:
    """Fresh state per scenario; tmp_path scopes the throwaway repo."""
    return _State(repo_root=tmp_path)


# ---------------------------------------------------------------------------
# Helpers — build a hermetic mini-repo with a single release tag + paydown doc
# ---------------------------------------------------------------------------


def _init_repo_with_tag(repo_root: Path, tag_date: date, tag_name: str = "v2026.5.18") -> None:
    """Initialise a git repo at ``repo_root`` with one commit + one release tag.

    The tag's creatordate is set to ``tag_date`` via ``GIT_COMMITTER_DATE``
    on the commit and the tag-creation invocation, so
    ``git for-each-ref --sort=-creatordate`` picks it up at the expected
    date.
    """
    env = {
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
        "GIT_AUTHOR_DATE": f"{tag_date.isoformat()}T12:00:00+00:00",
        "GIT_COMMITTER_DATE": f"{tag_date.isoformat()}T12:00:00+00:00",
        "PATH": "/usr/bin:/bin:/usr/local/bin",
    }
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo_root, env=env, check=True)
    # Seed a commit so we have something to tag.
    (repo_root / "seed.txt").write_text("seed\n")
    subprocess.run(["git", "add", "seed.txt"], cwd=repo_root, env=env, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "seed"],
        cwd=repo_root,
        env=env,
        check=True,
    )
    subprocess.run(
        ["git", "tag", "-a", tag_name, "-m", f"release {tag_name}"],
        cwd=repo_root,
        env=env,
        check=True,
    )


def _write_paydown_doc(
    repo_root: Path,
    snapshot_date: date,
    *,
    extension_until: date | None = None,
) -> None:
    """Write a minimal paydown doc with the requested snapshot header."""
    doc_dir = repo_root / "docs" / "architecture"
    doc_dir.mkdir(parents=True, exist_ok=True)
    extension_block = ""
    if extension_until is not None:
        extension_block = (
            f"\n<!-- expected-out-of-date-until: {extension_until.isoformat()} -->\n"
            "Snapshot kept until the next paydown wave lands.\n"
        )
    contents = (
        "# Grandfathering paydown — state + plan\n"
        "\n"
        f"## State (as of {snapshot_date.isoformat()}, post-deadbeef)\n"
        f"{extension_block}\n"
        "(table omitted for hermetic test)\n"
    )
    (doc_dir / "grandfathering-paydown.md").write_text(contents)


# ---------------------------------------------------------------------------
# Givens
# ---------------------------------------------------------------------------


@given("a paydown doc snapshot dated within 7 days of the most recent release tag")
def _given_fresh_snapshot(paydown_state: _State) -> None:
    assert paydown_state.repo_root is not None
    paydown_state.tag_date = date(2026, 5, 21)
    paydown_state.snapshot_date = date(2026, 5, 24)
    _init_repo_with_tag(paydown_state.repo_root, paydown_state.tag_date)
    _write_paydown_doc(paydown_state.repo_root, paydown_state.snapshot_date)


@given("a paydown doc snapshot dated 50 days before the most recent release tag")
def _given_stale_snapshot(paydown_state: _State) -> None:
    assert paydown_state.repo_root is not None
    paydown_state.tag_date = date(2026, 5, 21)
    paydown_state.snapshot_date = paydown_state.tag_date - timedelta(days=50)
    _init_repo_with_tag(paydown_state.repo_root, paydown_state.tag_date)
    _write_paydown_doc(paydown_state.repo_root, paydown_state.snapshot_date)


@given("no expected-out-of-date-until comment is present")
def _given_no_extension(paydown_state: _State) -> None:
    # Composed-in by the stale-snapshot Given above; this step exists to
    # make the scenario read naturally and to assert no extension snuck in.
    assert paydown_state.repo_root is not None
    doc = (paydown_state.repo_root / "docs" / "architecture" / "grandfathering-paydown.md").read_text()
    assert "expected-out-of-date-until" not in doc, (
        "stale-snapshot fixture must not carry an extension comment for this scenario"
    )


@given("an expected-out-of-date-until comment dated in the future is present")
def _given_extension_present(paydown_state: _State) -> None:
    assert paydown_state.repo_root is not None
    assert paydown_state.snapshot_date is not None
    # Rewrite the doc with the same snapshot date + a future extension.
    paydown_state.extension_until = date(2099, 12, 31)
    _write_paydown_doc(
        paydown_state.repo_root,
        paydown_state.snapshot_date,
        extension_until=paydown_state.extension_until,
    )


# ---------------------------------------------------------------------------
# When
# ---------------------------------------------------------------------------


@when("I run the paydown-doc currency check")
def _when_run_check(paydown_state: _State) -> None:
    assert paydown_state.repo_root is not None
    # Pin ``today`` to the snapshot's tag-relative neighbourhood so the
    # extension-comment future-date logic doesn't depend on the wall
    # clock during CI.
    today = date(2026, 5, 24)
    exit_code, lines = check_currency(paydown_state.repo_root, today=today)
    paydown_state.exit_code = exit_code
    paydown_state.output_lines = lines


# ---------------------------------------------------------------------------
# Thens
# ---------------------------------------------------------------------------


@then("the check exits 0")
def _then_exit_zero(paydown_state: _State) -> None:
    assert paydown_state.exit_code == 0, f"expected exit 0, got {paydown_state.exit_code}; output:\n" + "\n".join(
        paydown_state.output_lines
    )


@then("the check exits 1")
def _then_exit_one(paydown_state: _State) -> None:
    assert paydown_state.exit_code == 1, f"expected exit 1, got {paydown_state.exit_code}; output:\n" + "\n".join(
        paydown_state.output_lines
    )


@then("the output reports the snapshot is within the freshness window")
def _then_within_window(paydown_state: _State) -> None:
    joined = "\n".join(paydown_state.output_lines)
    assert "within" in joined and "window" in joined, f"expected freshness-window note in output, got:\n{joined}"


@then("the output names the snapshot date and the release tag date")
def _then_names_dates(paydown_state: _State) -> None:
    joined = "\n".join(paydown_state.output_lines)
    assert paydown_state.snapshot_date is not None and paydown_state.tag_date is not None
    assert paydown_state.snapshot_date.isoformat() in joined, (
        f"expected snapshot date {paydown_state.snapshot_date} in output:\n{joined}"
    )
    assert paydown_state.tag_date.isoformat() in joined, (
        f"expected tag date {paydown_state.tag_date} in output:\n{joined}"
    )


@then("the output carries the fix / next / run action markers")
def _then_action_markers(paydown_state: _State) -> None:
    joined = "\n".join(paydown_state.output_lines)
    for marker in ("fix:", "next:", "run:"):
        assert marker in joined, f"expected F21 action marker '{marker}' in failure output:\n{joined}"


@then("the output reports the extension comment was honoured")
def _then_extension_honoured(paydown_state: _State) -> None:
    joined = "\n".join(paydown_state.output_lines)
    assert "expected-out-of-date-until" in joined, f"expected extension-comment note in output, got:\n{joined}"
