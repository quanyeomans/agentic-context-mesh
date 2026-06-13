"""Unit tests for the paved-road query surface (#499 Phase 2).

Covers three pieces of the catalogue-driven paved road:

  1. ``RuleEntry`` round-trips the two new optional fields
     (``exemplar`` + ``task_type``).
  2. Every ``task_type`` tag used anywhere in the catalogue is a member
     of the closed :data:`TASK_TYPES` vocabulary — the typo guard that
     keeps the query surface honest forever.
  3. ``scripts/checks/rules.py`` filters by ``--task`` and surfaces a
     rule's exemplar via ``--rule``.
  4. ``scripts/checks/run_checks.py`` prints the ``paved-road:`` footer
     for a FAILING rule that carries an exemplar, and omits it for a
     failing rule that does not.

Surface discipline: items 1-3 drive the public catalogue helpers and
the ``rules.py`` render functions / ``main``. Item 4 drives the runner's
per-rule output-contract function ``_run_one`` — the documented "Output
contract (F83)" boundary in ``run_checks.py`` — against REAL throwaway
check scripts (a real subprocess, no monkeypatch, no production-behaviour
mutation). The footer branch is only reachable when a check actually
exits non-zero, so the test stages a script that does exactly that.
"""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_CHECKS_DIR = _REPO_ROOT / "scripts" / "checks"
sys.path.insert(0, str(_CHECKS_DIR))

import rules  # noqa: E402
import run_checks  # noqa: E402
from _rule_catalogue import ALL_ENTRIES, TASK_TYPES, RuleEntry  # noqa: E402

# ── 1. RuleEntry round-trips the new optional fields ───────────────────


def test_rule_entry_round_trips_exemplar_and_task_type() -> None:
    """A constructed ``RuleEntry`` preserves ``exemplar`` and
    ``task_type`` exactly, and defaults them when omitted."""
    explicit = RuleEntry(
        id="FX",
        gate="fx",
        check="fx",
        category="test-discipline",
        scope="per-file",
        summary="example rule",
        exemplar="tests/example/test_example.py",
        task_type=("writing-a-test", "adding-a-connector"),
    )
    assert explicit.exemplar == "tests/example/test_example.py"
    assert explicit.task_type == ("writing-a-test", "adding-a-connector")

    defaulted = RuleEntry(
        id="FY",
        gate="fy",
        check="fy",
        category="test-discipline",
        scope="per-file",
        summary="example rule with no paved-road metadata",
    )
    assert defaulted.exemplar is None
    assert defaulted.task_type == ()


# ── 2. every task_type tag is in the closed vocabulary ─────────────────


def test_every_catalogue_task_type_is_in_the_closed_vocabulary() -> None:
    """No catalogue entry may tag a task-type outside :data:`TASK_TYPES`.

    This is the typo guard: a misspelled tag (``writing-a-tset``) would
    silently make ``rules.py --task writing-a-test`` miss the rule. The
    closed vocabulary plus this assertion makes that impossible.
    """
    vocab = set(TASK_TYPES)
    offenders: list[tuple[str, str]] = []
    for entry in ALL_ENTRIES:
        for tag in entry.task_type:
            if tag not in vocab:
                offenders.append((entry.id, tag))
    assert not offenders, (
        f"catalogue entries tag a task-type outside TASK_TYPES: {offenders!r}. "
        f"Use one of {sorted(vocab)!r}, or add the new task to TASK_TYPES first."
    )


def test_at_least_one_rule_is_tagged_per_used_task() -> None:
    """The high-traffic backfill is real: at least the connector-build
    and test-writing tasks resolve to rules (guards an empty backfill)."""
    connector_rules = [e for e in ALL_ENTRIES if "adding-a-connector" in e.task_type]
    test_rules = [e for e in ALL_ENTRIES if "writing-a-test" in e.task_type]
    assert connector_rules, "no rule tagged adding-a-connector — backfill missing"
    assert test_rules, "no rule tagged writing-a-test — backfill missing"


# ── 3. rules.py query surface ──────────────────────────────────────────


def test_rules_task_lists_only_rules_tagged_with_that_task() -> None:
    """``rules.py --task <t>`` lists exactly the rules tagged ``t`` — and
    none tagged with a different task."""
    out = rules.render_task("adding-a-connector")
    tagged = [e for e in ALL_ENTRIES if "adding-a-connector" in e.task_type]
    untagged_only = [
        e for e in ALL_ENTRIES if "adding-a-feature-flag" in e.task_type and "adding-a-connector" not in e.task_type
    ]
    assert tagged, "fixture precondition: expected at least one connector rule"
    for entry in tagged:
        assert entry.id in out, f"{entry.id} (tagged adding-a-connector) missing from --task output"
    for entry in untagged_only:
        assert entry.id not in out, f"{entry.id} (NOT a connector rule) leaked into --task adding-a-connector"


def test_rules_task_rejects_unknown_task() -> None:
    """An unknown task-type yields an actionable error, not a crash or a
    silent empty listing."""
    out = rules.render_task("not-a-real-task")
    assert out.startswith("unknown task-type")
    assert "--list-tasks" in out


def test_rules_rule_shows_the_exemplar() -> None:
    """``rules.py --rule <Fid>`` surfaces that rule's curated exemplar
    path so an agent knows which file to copy."""
    out = rules.render_rule("F46")
    entry = next(e for e in ALL_ENTRIES if e.id == "F46")
    assert entry.exemplar is not None, "fixture precondition: F46 should carry an exemplar"
    assert entry.exemplar in out
    assert "F46" in out


def test_rules_rule_rejects_unknown_id() -> None:
    """An unknown rule id yields an actionable error."""
    out = rules.render_rule("F9999")
    assert out.startswith("unknown rule id")


def test_rules_list_tasks_covers_the_whole_vocabulary() -> None:
    """``--list-tasks`` names every member of the closed vocabulary."""
    out = rules.render_list_tasks()
    for task in TASK_TYPES:
        assert task in out, f"task {task!r} missing from --list-tasks output"


def test_rules_main_requires_a_mode() -> None:
    """Calling ``rules.py`` with no mode flag is a usage error (argparse
    exits non-zero) — the surface never runs an undefined query."""
    with pytest.raises(SystemExit) as excinfo:
        rules.main([])
    assert excinfo.value.code != 0


# ── 4. run_checks.py paved-road footer ─────────────────────────────────


def _failing_check_script(tmp_path: Path, name: str) -> Path:
    """Stage a real check script under ``scripts/checks/`` that exits
    non-zero. Returned for cleanup by the caller. ``tmp_path`` only
    keys the unique name so parallel tests don't collide."""
    script = _CHECKS_DIR / name
    script.write_text("import sys\nsys.exit(1)\n", encoding="utf-8")
    return script


def _run_one_capture(entry: RuleEntry) -> str:
    """Dispatch ``entry`` through the runner's per-rule output-contract
    function and capture everything it prints."""
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        run_checks._run_one(entry, skip_coverage=True)
    return buffer.getvalue()


def test_footer_present_for_failing_rule_with_exemplar(tmp_path: Path) -> None:
    """A FAILING rule that carries an exemplar prints the ``paved-road:``
    footer pointing at ``rules.py --rule <id>``."""
    name = "check__pavedroad_fail_with_exemplar.py"
    script = _failing_check_script(tmp_path, name)
    try:
        entry = RuleEntry(
            id="FXFAIL",
            gate="fxfail",
            check="_pavedroad_fail_with_exemplar",
            category="test-discipline",
            scope="per-file",
            summary="synthetic failing rule with an exemplar",
            exemplar="tests/example/test_example.py",
        )
        out = _run_one_capture(entry)
        assert "FAIL [FXFAIL]" in out
        assert "paved-road: python3 scripts/checks/rules.py --rule FXFAIL" in out
    finally:
        script.unlink()


def test_footer_absent_for_failing_rule_without_exemplar(tmp_path: Path) -> None:
    """A FAILING rule with NO exemplar prints its verdict but NO
    ``paved-road:`` footer — the footer is opt-in via the exemplar."""
    name = "check__pavedroad_fail_no_exemplar.py"
    script = _failing_check_script(tmp_path, name)
    try:
        entry = RuleEntry(
            id="FXBARE",
            gate="fxbare",
            check="_pavedroad_fail_no_exemplar",
            category="test-discipline",
            scope="per-file",
            summary="synthetic failing rule with no exemplar",
        )
        out = _run_one_capture(entry)
        assert "FAIL [FXBARE]" in out
        assert "paved-road:" not in out
    finally:
        script.unlink()
