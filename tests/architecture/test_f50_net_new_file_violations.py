"""Unit tests for F50 — net-new-file violation gate.

Closes the per-file-shrink-only loophole. New files cannot accrete debt
in any of the per-file F-rule baselines; F50 fires at pre-commit and
in CI Stage 0.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.checks.check_f50_net_new_file_violations import (
    _load_all_baselines,
    _read_baseline,
    find_net_new_violations,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# _read_baseline — handles comments, blank lines, whitespace
# ---------------------------------------------------------------------------


def test_read_baseline_returns_non_comment_non_blank_paths(tmp_path: Path) -> None:
    baseline = tmp_path / "test-baseline-files.txt"
    baseline.write_text(
        """# Header comment
# explanation line

kairix/foo.py
kairix/bar.py
# trailing comment
""",
        encoding="utf-8",
    )
    assert _read_baseline(baseline) == {"kairix/foo.py", "kairix/bar.py"}


def test_read_baseline_strips_trailing_whitespace(tmp_path: Path) -> None:
    """Editor-introduced trailing whitespace must not desync baseline matches."""
    baseline = tmp_path / "test-baseline-files.txt"
    baseline.write_text("kairix/foo.py   \nkairix/bar.py\t\n", encoding="utf-8")
    assert _read_baseline(baseline) == {"kairix/foo.py", "kairix/bar.py"}


def test_read_baseline_returns_empty_for_missing_file(tmp_path: Path) -> None:
    """Sabotage proof: if the function returned {""} or raised, this test
    would catch the change."""
    assert _read_baseline(tmp_path / "nope.txt") == set()


# ---------------------------------------------------------------------------
# find_net_new_violations — core logic
# ---------------------------------------------------------------------------


def test_added_file_not_in_any_baseline_returns_no_violations() -> None:
    """The healthy case: a new file that doesn't appear in any baseline."""
    baselines = {
        "f30-files.txt": {"kairix/old/cli.py"},
        "f47-files.txt": {"tests/integration/test_legacy.py"},
    }
    result = find_net_new_violations(["kairix/new/cli.py"], baselines)
    assert result == {}


def test_added_file_present_in_one_baseline_flagged() -> None:
    """The forbidden case F50 exists to catch."""
    baselines = {
        "f30-files.txt": {"kairix/old/cli.py"},
        "f47-files.txt": {"kairix/new/cli.py"},  # forbidden: net-new file in a baseline
    }
    result = find_net_new_violations(["kairix/new/cli.py"], baselines)
    assert result == {"f47-files.txt": ["kairix/new/cli.py"]}


def test_added_file_present_in_multiple_baselines_reports_all() -> None:
    """A new file violating multiple rules surfaces all of them."""
    baselines = {
        "f30-files.txt": {"kairix/new/cli.py"},
        "f47-files.txt": {"kairix/new/cli.py"},
    }
    result = find_net_new_violations(["kairix/new/cli.py"], baselines)
    assert result == {
        "f30-files.txt": ["kairix/new/cli.py"],
        "f47-files.txt": ["kairix/new/cli.py"],
    }


def test_empty_added_set_returns_no_violations() -> None:
    """Pre-commit no-op case: no new files staged."""
    baselines = {"f30-files.txt": {"kairix/anything.py"}}
    result = find_net_new_violations([], baselines)
    assert result == {}


def test_added_files_sorted_in_output() -> None:
    """Output ordering is deterministic so CI logs are diffable."""
    baselines = {"f30-files.txt": {"kairix/b.py", "kairix/a.py", "kairix/c.py"}}
    result = find_net_new_violations(
        ["kairix/c.py", "kairix/a.py", "kairix/b.py"],
        baselines,
    )
    assert result == {"f30-files.txt": ["kairix/a.py", "kairix/b.py", "kairix/c.py"]}


# ---------------------------------------------------------------------------
# _load_all_baselines — walks the real baseline dir
# ---------------------------------------------------------------------------


def test_load_all_baselines_walks_real_baseline_dir() -> None:
    """Sanity check that the canonical baseline dir is walked and at least
    one known F-rule baseline is parsed (smoke test against the real tree)."""
    baselines = _load_all_baselines()
    # F30 baseline file exists in the repo (currently empty post-Wave-0,
    # but the file remains as the canonical paydown record).
    assert "f30-operator-outcome-tests-files.txt" in baselines
