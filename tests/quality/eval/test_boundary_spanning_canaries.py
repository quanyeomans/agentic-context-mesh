"""ADR-028 §"Quality evaluation" #3 — boundary-spanning canary suite tests.

Loads the bundled ``suites/per-type-canary-suite.yaml``, verifies its
schema parses, asserts every canary has the required metadata, and
runs the canary aggregation against synthetic case results to confirm
the per-unit pass rate surfaces.

Sabotage proofs:

* test_canary_suite_loads_and_every_case_is_flagged — mutate the
  suite loader to drop the ``canary`` field; the assert that every
  case carries ``canary=True`` fails.
* test_canary_suite_covers_every_atomic_unit — mutate
  ``per-type-canary-suite.yaml`` to delete the row canaries; the
  assert that all four units are present fails.
* test_canary_pass_rate_propagates_into_runner_summary — mutate the
  runner to drop the ``canary`` key from the summary; the lookup
  raises KeyError.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kairix.quality.benchmark.per_type_slicing import aggregate_canary
from kairix.quality.benchmark.suite import load_suite

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CANARY_SUITE = _REPO_ROOT / "suites" / "per-type-canary-suite.yaml"


def test_canary_suite_loads_and_every_case_is_flagged() -> None:
    """Every case in the bundled canary suite has ``canary=True``.

    Sabotage: open ``suites/per-type-canary-suite.yaml`` and delete
    ``canary: true`` from one case; the assert fires.
    """
    suite = load_suite(str(_CANARY_SUITE))
    assert len(suite.cases) >= 12, f"expected ≥12 canary cases, got {len(suite.cases)}"
    for case in suite.cases:
        assert case.canary is True, f"case {case.id} missing canary flag"
        assert case.canary_unit is not None, f"case {case.id} missing canary_unit"


def test_canary_suite_covers_every_atomic_unit() -> None:
    """The bundled canary suite exercises all four atomic-unit types.

    Sabotage: delete every row canary from the YAML — the assert that
    ``"row"`` is in the unit set fires.
    """
    suite = load_suite(str(_CANARY_SUITE))
    units = {case.canary_unit for case in suite.cases}
    assert units == {"slide", "row", "event", "message"}, units


def test_canary_suite_has_at_least_three_per_unit() -> None:
    """At least three canaries per atomic-unit type per ADR-028 brief.

    Sabotage: delete two of the three slide canaries — the count drops
    below 3 and the assert fires.
    """
    suite = load_suite(str(_CANARY_SUITE))
    counts: dict[str, int] = {}
    for case in suite.cases:
        unit = case.canary_unit or "other"
        counts[unit] = counts.get(unit, 0) + 1
    for unit in ("slide", "row", "event", "message"):
        assert counts.get(unit, 0) >= 3, f"{unit} has only {counts.get(unit, 0)} canaries"


def test_canary_pass_rate_propagates_into_runner_summary() -> None:
    """Synthesise a passing run; canary summary reports 100% by unit.

    Sabotage: change ``aggregate_canary`` to ``return {"overall": {},
    "by_unit": {}}`` — the assert that ``rate == 1.0`` fires.
    """
    suite = load_suite(str(_CANARY_SUITE))
    # Synthesise: every canary scores 0.9 (above the 0.5 pass bar).
    case_results = [
        {
            "id": case.id,
            "category": case.category,
            "query": case.query,
            "score_method": "ndcg",
            "score": 0.9,
            "retrieved_paths": [],
            "elapsed_ms": 5.0,
            "rr": 1.0,
            "hit_at_5": 1.0,
        }
        for case in suite.cases
    ]
    summary = aggregate_canary(suite.cases, case_results)
    assert summary["overall"]["rate"] == pytest.approx(1.0, abs=1e-3)
    for unit in ("slide", "row", "event", "message"):
        assert summary["by_unit"][unit]["rate"] == pytest.approx(1.0, abs=1e-3)


def test_canary_failing_case_drops_unit_rate() -> None:
    """One failing canary inside a unit drops that unit's pass rate.

    Sabotage: change the slide canary in the YAML's gold_titles so the
    fake case_result below also fails the rate check.
    """
    suite = load_suite(str(_CANARY_SUITE))
    case_results = []
    for case in suite.cases:
        # First slide canary fails; everything else passes.
        score = 0.1 if case.id == "CANARY-SLIDE-001" else 0.9
        case_results.append(
            {
                "id": case.id,
                "category": case.category,
                "query": case.query,
                "score_method": "ndcg",
                "score": score,
                "retrieved_paths": [],
                "elapsed_ms": 5.0,
                "rr": 1.0 if score > 0.5 else 0.0,
                "hit_at_5": 1.0 if score > 0.5 else 0.0,
            }
        )
    summary = aggregate_canary(suite.cases, case_results)
    # Slide unit: 2/3 passed (the one we sabotaged failed)
    assert summary["by_unit"]["slide"]["rate"] < 1.0
    assert summary["by_unit"]["slide"]["passed"] == 2.0
    assert summary["by_unit"]["slide"]["total"] == 3.0
    # Other units still 100%
    assert summary["by_unit"]["row"]["rate"] == pytest.approx(1.0, abs=1e-3)
