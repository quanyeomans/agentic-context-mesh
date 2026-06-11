"""Unit tests for F49 (``scripts/checks/check_baseline_shrinking.py``).

F49 requires each release tag to reduce each of the three governed
baseline files by at least one entry vs the previous release tag, OR
keep all three at zero. The check runs at release time only, not
per-commit.

Each test creates a synthetic git repo in ``tmp_path``, plants synthetic
baseline files, tags one commit, mutates baselines for the second
commit, then invokes ``check_baselines`` with the synthetic prev-tag.

Sabotage-proof for each test case: see the inline comments. A mutation
of the production check that always returns success (e.g. dropping the
``failures`` accumulation) flips the FAIL cases to pass — confirming
that those assertions are load-bearing.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DETECTOR_PATH = _REPO_ROOT / "scripts" / "checks" / "check_baseline_shrinking.py"


def _load_detector():
    """Load the F49 detector module by file path."""
    spec = importlib.util.spec_from_file_location("_f49_detector", _DETECTOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["_f49_detector"] = module
    spec.loader.exec_module(module)
    return module


def _git(cwd: Path, *args: str) -> str:
    """Run ``git ...`` in ``cwd`` and return stdout, failing on non-zero."""
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _init_repo(tmp_path: Path) -> Path:
    """Initialise a fresh git repo at ``tmp_path``. Returns ``tmp_path``."""
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "commit.gpgsign", "false")
    return tmp_path


def _commit_baselines(
    repo: Path,
    f30_entries: list[str],
    f46_entries: list[str],
    f47_entries: list[str],
    message: str,
) -> str:
    """Write the three F49-governed baseline files with the given entries
    and commit. Returns the new commit SHA.
    """
    baseline_dir = repo / ".architecture" / "baseline"
    baseline_dir.mkdir(parents=True, exist_ok=True)
    (baseline_dir / "f30-operator-outcome-tests-files.txt").write_text(
        "# F30 baseline (synthetic for test).\n" + "\n".join(f30_entries) + ("\n" if f30_entries else "")
    )
    # Filenames are the catalogue-derived gate names (#499 Phase 0) —
    # the original hand-listed "F46-files.txt"/"F47-files.txt" never
    # matched the git-tracked baselines, which made two of the three
    # F49 legs vacuous on the case-sensitive Linux release runner.
    (baseline_dir / "f46-files.txt").write_text(
        "# F46 baseline (synthetic for test).\n" + "\n".join(f46_entries) + ("\n" if f46_entries else "")
    )
    (baseline_dir / "f47-integration-factory-files.txt").write_text(
        "# F47 baseline (synthetic for test).\n" + "\n".join(f47_entries) + ("\n" if f47_entries else "")
    )
    _git(repo, "add", "-A")
    # ``--allow-empty`` lets us advance HEAD even when the synthetic
    # baseline content is identical to the previous commit (e.g. the
    # zero-stays-zero and equal-non-zero scenarios re-commit the same
    # baseline contents to simulate a release boundary).
    _git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return _git(repo, "rev-parse", "HEAD").strip()


def test_shrunk_baseline_passes(tmp_path: Path) -> None:
    """When all three baselines shrink, the check passes.

    Sabotage-proof: if the production check always returned failure
    (i.e. flipped the shrink rule), this assertion would fail. The
    paired FAIL tests below confirm the rule fires on growth/equality.
    """
    detector = _load_detector()
    repo = _init_repo(tmp_path)

    _commit_baselines(
        repo,
        f30_entries=["kairix/a/cli.py", "kairix/b/cli.py", "kairix/c/cli.py"],
        f46_entries=["tests/bdd/steps/x.py"],
        f47_entries=["tests/integration/test_x.py"],
        message="seed baselines",
    )
    _git(repo, "tag", "-a", "v2026.5.1", "-m", "synthetic prev release")

    # HEAD: each baseline shrinks by one.
    _commit_baselines(
        repo,
        f30_entries=["kairix/a/cli.py", "kairix/b/cli.py"],
        f46_entries=[],
        f47_entries=[],
        message="pay down one entry per baseline",
    )

    exit_code, lines = detector.check_baselines(repo, prev_tag="v2026.5.1")
    assert exit_code == 0, "\n".join(lines)
    assert any("all governed baselines shrunk" in line for line in lines)


def test_grew_baseline_fails(tmp_path: Path) -> None:
    """When a baseline grows since the prev tag, the check FAILS and the
    failure output names the grown baseline + lists the still-present
    entries.

    Sabotage-proof inline: shrinking the grown baseline back to its prev
    count clears the failure.
    """
    detector = _load_detector()
    repo = _init_repo(tmp_path)

    _commit_baselines(
        repo,
        f30_entries=["kairix/a/cli.py"],
        f46_entries=[],
        f47_entries=[],
        message="seed",
    )
    _git(repo, "tag", "-a", "v2026.5.1", "-m", "synthetic prev release")

    # HEAD: F30 grows from 1 to 2.
    _commit_baselines(
        repo,
        f30_entries=["kairix/a/cli.py", "kairix/b/cli.py"],
        f46_entries=[],
        f47_entries=[],
        message="violation — baseline grew",
    )

    exit_code, lines = detector.check_baselines(repo, prev_tag="v2026.5.1")
    assert exit_code == 1, "\n".join(lines)
    output = "\n".join(lines)
    assert "f30-operator-outcome-tests-files.txt" in output
    assert "grew from 1 to 2" in output
    # The pre-existing entry must appear in the paydown-candidates list.
    assert "kairix/a/cli.py" in output

    # Sabotage-proof: restore the baseline to its previous size; the
    # check now passes.
    _commit_baselines(
        repo,
        f30_entries=[],
        f46_entries=[],
        f47_entries=[],
        message="paydown — restore parity",
    )
    exit_code_after, _ = detector.check_baselines(repo, prev_tag="v2026.5.1")
    assert exit_code_after == 0


def test_equal_non_zero_baseline_fails(tmp_path: Path) -> None:
    """A baseline that stays the same size at non-zero is a FAIL —
    every release must pay down at least one entry.

    Sabotage-proof inline: removing one entry flips the result to pass.
    """
    detector = _load_detector()
    repo = _init_repo(tmp_path)

    _commit_baselines(
        repo,
        f30_entries=["kairix/a/cli.py", "kairix/b/cli.py"],
        f46_entries=[],
        f47_entries=[],
        message="seed",
    )
    _git(repo, "tag", "-a", "v2026.5.1", "-m", "synthetic prev release")

    # HEAD: F30 stays at 2 entries.
    _commit_baselines(
        repo,
        f30_entries=["kairix/a/cli.py", "kairix/b/cli.py"],
        f46_entries=[],
        f47_entries=[],
        message="no progress",
    )

    exit_code, lines = detector.check_baselines(repo, prev_tag="v2026.5.1")
    assert exit_code == 1, "\n".join(lines)
    output = "\n".join(lines)
    assert "did not shrink" in output
    assert "stayed at 2" in output

    # Sabotage-proof: pay down one entry; the check now passes.
    _commit_baselines(
        repo,
        f30_entries=["kairix/a/cli.py"],
        f46_entries=[],
        f47_entries=[],
        message="paydown — one entry",
    )
    exit_code_after, _ = detector.check_baselines(repo, prev_tag="v2026.5.1")
    assert exit_code_after == 0


def test_zero_stays_zero_passes(tmp_path: Path) -> None:
    """A baseline that was zero at the prev tag and stays zero is OK —
    zero-to-zero does not require a paydown.

    Sabotage-proof: if the rule were "must shrink even from zero", a
    zero baseline would be impossible to release against. This assertion
    proves the rule's zero-stays-zero carve-out fires.
    """
    detector = _load_detector()
    repo = _init_repo(tmp_path)

    _commit_baselines(
        repo,
        f30_entries=[],
        f46_entries=[],
        f47_entries=[],
        message="seed — all zero",
    )
    _git(repo, "tag", "-a", "v2026.5.1", "-m", "synthetic prev release")

    _commit_baselines(
        repo,
        f30_entries=[],
        f46_entries=[],
        f47_entries=[],
        message="still all zero",
    )

    exit_code, lines = detector.check_baselines(repo, prev_tag="v2026.5.1")
    assert exit_code == 0, "\n".join(lines)


def test_missing_baseline_at_prev_tag_treated_as_zero(tmp_path: Path) -> None:
    """If a baseline file did not exist at the previous tag (e.g. F46
    introduced post-tag), it is treated as count 0 — so HEAD with 0
    entries passes (zero stays zero) but HEAD with any entries FAILS
    (grew from 0 to N).

    Sabotage-proof: prev=0/head=0 passes; prev=0/head=1 fails — both
    branches asserted.
    """
    detector = _load_detector()
    repo = _init_repo(tmp_path)

    # Seed: only the f30 baseline exists; F46/F47 do not.
    baseline_dir = repo / ".architecture" / "baseline"
    baseline_dir.mkdir(parents=True, exist_ok=True)
    (baseline_dir / "f30-operator-outcome-tests-files.txt").write_text("# F30 seed.\nkairix/a/cli.py\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed (F46/F47 absent)")
    _git(repo, "tag", "-a", "v2026.5.1", "-m", "synthetic prev release")

    # HEAD with F46 absent + F30 shrunk to 0 → pass.
    _commit_baselines(
        repo,
        f30_entries=[],
        f46_entries=[],
        f47_entries=[],
        message="paydown to zero across the board",
    )
    exit_code, _ = detector.check_baselines(repo, prev_tag="v2026.5.1")
    assert exit_code == 0

    # HEAD with F46 introduced with one entry → fail (grew from 0).
    _commit_baselines(
        repo,
        f30_entries=[],
        f46_entries=["tests/bdd/steps/new.py"],
        f47_entries=[],
        message="introduce F46 with one entry",
    )
    exit_code_after, lines_after = detector.check_baselines(repo, prev_tag="v2026.5.1")
    assert exit_code_after == 1
    assert any("f46-files.txt" in line and "grew from 0 to 1" in line for line in lines_after)


def test_first_release_passes(tmp_path: Path) -> None:
    """When HEAD has no prior release tag in its ancestry (first
    release), the check exits 0 with a "first release" notice.

    Sabotage-proof: if the check FAILED on absent prev-tag, every
    first-release attempt would be blocked. This assertion documents
    the intentional carve-out.
    """
    detector = _load_detector()
    repo = _init_repo(tmp_path)

    _commit_baselines(
        repo,
        f30_entries=["kairix/a/cli.py"],
        f46_entries=[],
        f47_entries=[],
        message="initial commit",
    )

    # No tag at all — _resolve_previous_tag returns None.
    exit_code, lines = detector.check_baselines(repo, prev_tag=None)
    assert exit_code == 0
    assert any("first release" in line for line in lines)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("", 0),
        ("# comment only\n", 0),
        ("kairix/a/cli.py\nkairix/b/cli.py\n", 2),
        ("# header\nkairix/a/cli.py\n\nkairix/b/cli.py\n", 2),
        ("   \n  # indented comment\nkairix/a/cli.py\n", 1),
    ],
)
def test_count_non_comment_lines(raw: str, expected: int) -> None:
    """``_count_non_comment_lines`` ignores blank + comment lines.

    Sabotage-proof: each parameter pair is an independent canary — if
    the parser stopped ignoring comments, the (comment-only → 0) case
    would fail with 1.
    """
    detector = _load_detector()
    assert detector._count_non_comment_lines(raw) == expected
