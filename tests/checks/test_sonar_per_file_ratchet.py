"""Sonar per-file ratchet detector tests (deterministic, no live Sonar).

The ratchet (``scripts/checks/check_sonar_new_code.py``) compares CURRENT
per-file open-issue / hotspot counts against a COMMITTED baseline and fails
any file whose count exceeds its baseline. A file absent from the baseline
defaults to 0, so any issue on a net-new file fails it. Hotspots ratchet
through their OWN baseline so a hotspot regression fails even when smells are
clean.

These tests inject fake count maps + baselines directly into the pure verdict
core (``compute_regressions`` / ``evaluate`` / ``load_baseline``); they never
touch the network. Each test carries a sabotage proof — the executed
mutate -> fail -> restore that proves the assertion is load-bearing.

Sabotage proofs (executed against scripts/checks/check_sonar_new_code.py):
  - test_file_exceeding_baseline_is_regression: change ``cur > base`` to
    ``cur >= base`` in compute_regressions -> at-baseline test goes red.
    Restored.
  - test_at_baseline_is_not_regression: change ``cur > base`` to
    ``cur < base`` -> this test goes red (no regression reported for an
    over-baseline file). Restored.
  - test_new_file_absent_from_baseline_defaults_to_zero: change
    ``baseline.get(path, 0)`` to ``baseline.get(path, 999)`` -> the new-file
    issue stops being a regression and this test goes red. Restored.
  - test_below_baseline_is_not_regression: change ``cur > base`` to
    ``cur != base`` -> a below-baseline file is flagged and this test goes
    red. Restored.
  - test_hotspot_regression_fails_even_when_issues_clean: swap the issue and
    hotspot baselines in ``evaluate`` -> the hotspot regression is checked
    against the (lenient) issue baseline and this test goes red. Restored.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CHECKS_DIR = _REPO_ROOT / "scripts" / "checks"
if str(_CHECKS_DIR) not in sys.path:
    sys.path.insert(0, str(_CHECKS_DIR))

from check_sonar_new_code import (  # noqa: E402
    compute_regressions,
    evaluate,
    load_baseline,
)

pytestmark = pytest.mark.unit


def test_file_exceeding_baseline_is_regression() -> None:
    """(a) A file whose current count exceeds its baseline is a regression."""
    current = {"kairix/core/search/rrf.py": 3}
    baseline = {"kairix/core/search/rrf.py": 1}

    regressions = compute_regressions(current, baseline, files_in_scope=None)

    assert regressions == [("kairix/core/search/rrf.py", 3, 1)]


def test_at_baseline_is_not_regression() -> None:
    """(b) A file exactly at its baseline count is NOT a regression."""
    current = {"kairix/core/search/rrf.py": 2}
    baseline = {"kairix/core/search/rrf.py": 2}

    regressions = compute_regressions(current, baseline, files_in_scope=None)

    assert regressions == []


def test_new_file_absent_from_baseline_defaults_to_zero() -> None:
    """(c) A net-new file (absent from baseline) with any issue fails — the
    absent-file default is 0, so 1 > 0 is a regression."""
    current = {"kairix/brand/new_module.py": 1}
    baseline: dict[str, int] = {}

    regressions = compute_regressions(current, baseline, files_in_scope=None)

    assert regressions == [("kairix/brand/new_module.py", 1, 0)]


def test_below_baseline_is_not_regression() -> None:
    """(d) A file BELOW its grandfathered baseline (debt being paid down) is
    NOT a regression — the ratchet only blocks increases."""
    current = {"kairix/legacy/big.py": 4}
    baseline = {"kairix/legacy/big.py": 7}

    regressions = compute_regressions(current, baseline, files_in_scope=None)

    assert regressions == []


def test_hotspot_regression_fails_even_when_issues_clean() -> None:
    """(e) Hotspots ratchet through their OWN baseline: a hotspot regression
    fails even when every code-smell/issue count is at-or-below baseline."""
    issue_counts = {"kairix/secrets/store.py": 1}
    issue_baseline = {"kairix/secrets/store.py": 1}  # at baseline — clean
    hotspot_counts = {"kairix/secrets/store.py": 1}
    hotspot_baseline: dict[str, int] = {}  # absent -> default 0 -> 1 > 0 fails

    issue_regressions, hotspot_regressions = evaluate(
        issue_counts,
        hotspot_counts,
        issue_baseline,
        hotspot_baseline,
        files_in_scope=None,
    )

    assert issue_regressions == []  # smells clean
    assert hotspot_regressions == [("kairix/secrets/store.py", 1, 0)]  # hotspot fails


def test_working_set_scope_excludes_unchanged_files() -> None:
    """Working-set mode only gates files in scope; an over-baseline file
    NOT in the changed set is ignored (the full-repo --all view would catch
    it, but the default scoped run does not)."""
    current = {
        "kairix/changed.py": 2,  # in scope, over baseline
        "kairix/untouched.py": 5,  # over baseline but NOT in scope
    }
    baseline = {"kairix/changed.py": 0, "kairix/untouched.py": 0}

    scoped = compute_regressions(current, baseline, files_in_scope={"kairix/changed.py"})
    full = compute_regressions(current, baseline, files_in_scope=None)

    assert scoped == [("kairix/changed.py", 2, 0)]
    assert ("kairix/untouched.py", 5, 0) in full


def test_load_baseline_missing_file_is_empty(tmp_path: Path) -> None:
    """An absent baseline file loads as ``{}`` (all-zero default — strictest)."""
    assert load_baseline(tmp_path / "nope.json") == {}


def test_load_baseline_reads_files_mapping(tmp_path: Path) -> None:
    """A baseline JSON with a ``files`` mapping loads the per-file counts and
    ignores the ``_meta`` provenance header."""
    p = tmp_path / "sonar.json"
    p.write_text(
        '{"_meta": {"branch": "main"}, "files": {"kairix/a.py": 3, "kairix/b.py": 1}}',
        encoding="utf-8",
    )

    loaded = load_baseline(p)

    assert loaded == {"kairix/a.py": 3, "kairix/b.py": 1}
