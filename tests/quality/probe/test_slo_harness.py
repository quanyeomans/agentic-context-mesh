"""Unit tests for the SLO-harness measurement engine (PLA-256).

Exercises the pure engine — breadcrumb resolution, recall@k / NDCG@k,
cold/warm/concurrency latency structure, affordance counting, and report
projection — with deterministic injected probes and recall callables. No
wall-clock-ceiling assertions (F82): latency cells are asserted on
STRUCTURE (phase / concurrency / sample count), never elapsed-vs-numeric.
"""

from __future__ import annotations

import math

import pytest

from kairix.quality.probe.slo_harness import (
    PHASE_COLD,
    PHASE_WARM,
    CommandCall,
    CommandProbe,
    GroundTruthFact,
    build_report,
    is_resolvable_breadcrumb,
    measure_command,
    measure_recall,
)
from tests.fakes import FakeFactHit, FakeFactRecord

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# is_resolvable_breadcrumb
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("kb://engagement-alpha/overview.md", True),
        ("turn://eng-alpha-s001-t003", True),
        ("memory://synthetic/0001", True),
        ("", False),
        ("   ", False),
        ("none", False),
        ("UNKNOWN", False),
        (None, False),
        (123, False),
    ],
)
def test_is_resolvable_breadcrumb(value: object, expected: bool) -> None:
    """A breadcrumb is resolvable only when it is a non-placeholder string."""
    assert is_resolvable_breadcrumb(value) is expected


# ---------------------------------------------------------------------------
# measure_recall — recall@k + NDCG@k
# ---------------------------------------------------------------------------


def _hit(entity: str, attribute: str, value: str) -> FakeFactHit:
    return FakeFactHit(
        record=FakeFactRecord(id=f"{entity}:{attribute}", entity=entity, attribute=attribute, value=value),
        score=1.0,
    )


def test_measure_recall_perfect_when_relevant_fact_ranks_first() -> None:
    """A relevant hit at rank 0 yields recall@k=1.0 and NDCG@k=1.0."""
    gt = [GroundTruthFact(entity="client-omega", attribute="primary-cloud", value="cloud-zeta")]
    row = measure_recall("suite", gt, lambda _q: [_hit("client-omega", "primary-cloud", "cloud-zeta")], k=5)
    assert row.n_facts == 1
    assert row.recall_at_k == 1.0
    assert row.ndcg_at_k == 1.0


def test_measure_recall_zero_when_fact_absent() -> None:
    """A query that returns only non-matching hits scores 0 on both metrics."""
    gt = [GroundTruthFact(entity="client-omega", attribute="primary-cloud", value="cloud-zeta")]
    row = measure_recall("suite", gt, lambda _q: [_hit("other-entity", "other-attr", "other-value")], k=5)
    assert row.recall_at_k == 0.0
    assert row.ndcg_at_k == 0.0


def test_measure_recall_discounts_lower_ranks() -> None:
    """A relevant hit at rank 2 still counts for recall but NDCG is discounted."""
    gt = [GroundTruthFact(entity="client-omega", attribute="primary-cloud", value="cloud-zeta")]

    def recall_fn(_q: str) -> list[FakeFactHit]:
        return [
            _hit("filler-a", "x", "y"),
            _hit("filler-b", "x", "y"),
            _hit("client-omega", "primary-cloud", "cloud-zeta"),
        ]

    row = measure_recall("suite", gt, recall_fn, k=5)
    assert row.recall_at_k == 1.0
    # rank index 2 → position 3 → 1 / log2(4) = 0.5
    assert row.ndcg_at_k == round(1.0 / math.log2(4), 4)


def test_measure_recall_respects_k_cutoff() -> None:
    """A relevant hit beyond the k cutoff is a miss."""
    gt = [GroundTruthFact(entity="client-omega", attribute="primary-cloud", value="cloud-zeta")]

    def recall_fn(_q: str) -> list[FakeFactHit]:
        return [_hit("filler", "x", "y"), _hit("client-omega", "primary-cloud", "cloud-zeta")]

    row = measure_recall("suite", gt, recall_fn, k=1)
    assert row.recall_at_k == 0.0


def test_measure_recall_substring_match_counts() -> None:
    """A retrieved value that contains the ground-truth value counts as a hit."""
    gt = [GroundTruthFact(entity="engagement-alpha", attribute="budget", value="$480k")]
    row = measure_recall(
        "suite", gt, lambda _q: [_hit("engagement-alpha", "budget", "$480k fixed-scope, 12 weeks")], k=5
    )
    assert row.recall_at_k == 1.0


def test_measure_recall_empty_suite_is_zero() -> None:
    """An empty ground-truth suite reports zero facts and zero scores."""
    row = measure_recall("suite", [], lambda _q: [], k=5)
    assert row.n_facts == 0
    assert row.recall_at_k == 0.0
    assert row.ndcg_at_k == 0.0


def test_measure_recall_empty_store_reports_na_not_zero() -> None:
    """An empty fact store (#727) yields a skipped N/A row, not a 0.0 regression.

    Sabotage-proof: drop the ``store_populated`` guard in ``measure_recall``
    → the row scores 0.0/0.0 with ``skipped=False`` and both the
    ``skipped is True`` and ``recall_at_k is None`` assertions fail.
    """
    gt = [GroundTruthFact(entity="client-omega", attribute="primary-cloud", value="cloud-zeta")]
    row = measure_recall("real-fact-store", gt, lambda _q: [], k=5, store_populated=False)
    assert row.skipped is True
    assert row.n_facts == 1
    payload = row.to_dict()
    assert payload["recall_at_k"] is None
    assert payload["ndcg_at_k"] is None
    assert payload["skipped"] is True


def test_measure_recall_populated_store_reports_real_number() -> None:
    """A populated store still scores a real recall number (not skipped)."""
    gt = [GroundTruthFact(entity="client-omega", attribute="primary-cloud", value="cloud-zeta")]
    row = measure_recall(
        "real-fact-store",
        gt,
        lambda _q: [_hit("client-omega", "primary-cloud", "cloud-zeta")],
        k=5,
        store_populated=True,
    )
    assert row.skipped is False
    assert row.recall_at_k == 1.0
    assert row.to_dict()["recall_at_k"] == 1.0


def test_measure_recall_ignores_hits_missing_fact_fields() -> None:
    """A retrieved object lacking entity/attribute/value is never a match.

    Pins the defensive ``except AttributeError: return False`` branch in the
    fact-match relation — a malformed hit must not be counted as recall.
    """
    gt = [GroundTruthFact(entity="client-omega", attribute="primary-cloud", value="cloud-zeta")]
    row = measure_recall("suite", gt, lambda _q: [object()], k=5)
    assert row.recall_at_k == 0.0
    assert row.ndcg_at_k == 0.0


# ---------------------------------------------------------------------------
# measure_command — cold/warm/concurrency latency + affordance
# ---------------------------------------------------------------------------


def _probe(name: str, breadcrumbs: tuple[str | None, ...], payloads: tuple[str, ...]) -> CommandProbe:
    return CommandProbe(name=name, payloads=payloads, run=lambda _p: CommandCall(breadcrumbs=breadcrumbs))


def test_measure_command_emits_cold_warm_c1_warm_cn_rows() -> None:
    """Each command yields exactly the cold, warm-c1, and warm-cN cells."""
    probe = _probe("search", ("kb://a",), payloads=("q1", "q2", "q3"))
    rows, _affordance = measure_command(probe, concurrency_n=5)

    assert [(r.phase, r.concurrency) for r in rows] == [
        (PHASE_COLD, 1),
        (PHASE_WARM, 1),
        (PHASE_WARM, 5),
    ]
    # Cold is a single observation; warm re-runs the whole workload.
    assert rows[0].stats.n == 1
    assert rows[1].stats.n == 3
    assert rows[2].stats.n == 3
    assert all(r.command == "search" for r in rows)


def test_measure_command_affordance_counts_resolvable_breadcrumbs() -> None:
    """Affordance counts every agent-facing record across the warm-c1 calls."""
    probe = _probe("search", ("kb://a", None), payloads=("q1", "q2"))
    _rows, affordance = measure_command(probe, concurrency_n=3)

    # 2 payloads x 2 breadcrumbs each = 4 records, half resolvable.
    assert affordance.total_records == 4
    assert affordance.resolvable == 2
    assert affordance.pct_resolvable == 50.0


def test_measure_command_full_breadcrumbs_are_100_pct() -> None:
    """All-resolvable breadcrumbs report 100% completeness."""
    probe = _probe("recall", ("turn://t1", "turn://t2"), payloads=("e1",))
    _rows, affordance = measure_command(probe, concurrency_n=2)
    assert affordance.pct_resolvable == 100.0


def test_measure_command_zero_records_is_vacuously_complete() -> None:
    """A command that surfaces no records is vacuously 100% complete."""
    probe = _probe("brief", (), payloads=("t1",))
    _rows, affordance = measure_command(probe, concurrency_n=2)
    assert affordance.total_records == 0
    assert affordance.pct_resolvable == 100.0


def test_measure_command_rejects_empty_payloads() -> None:
    """A probe with no payloads cannot be measured."""
    probe = CommandProbe(name="search", payloads=(), run=lambda _p: CommandCall())
    with pytest.raises(ValueError, match="no payloads"):
        measure_command(probe, concurrency_n=5)


# ---------------------------------------------------------------------------
# build_report — orchestration + projection
# ---------------------------------------------------------------------------


def _report():
    probes = (
        _probe("search", ("kb://a",), payloads=("q1", "q2")),
        _probe("recall", ("turn://t1", None), payloads=("e1",)),
    )
    gt = [GroundTruthFact(entity="client-omega", attribute="primary-cloud", value="cloud-zeta")]
    suites = [("synthetic", gt, lambda _q: [_hit("client-omega", "primary-cloud", "cloud-zeta")], True)]
    return build_report(probes=probes, recall_suites=suites, concurrency_n=4, recall_k=5)


def test_build_report_collects_all_three_dimensions() -> None:
    """The report carries latency, affordance, and recall rows together."""
    report = _report()
    # 2 commands x 3 latency cells each.
    assert len(report.latency) == 6
    assert {a.command for a in report.affordance} == {"search", "recall"}
    assert report.recall[0].recall_at_k == 1.0
    assert report.concurrency_n == 4
    assert report.recall_k == 5


def test_build_report_rejects_bad_concurrency() -> None:
    with pytest.raises(ValueError, match="concurrency_n"):
        build_report(probes=(), recall_suites=[], concurrency_n=0)


def test_build_report_rejects_bad_k() -> None:
    with pytest.raises(ValueError, match="recall_k"):
        build_report(probes=(), recall_suites=[], concurrency_n=5, recall_k=0)


def test_report_to_dict_round_trips_metrics() -> None:
    """The JSON projection carries every metric the CLI prints."""
    payload = _report().to_dict()
    assert payload["concurrency_n"] == 4
    assert payload["recall_k"] == 5
    assert len(payload["latency"]) == 6
    assert payload["recall"][0]["recall_at_k"] == 1.0
    # The half-missing recall breadcrumb is reflected in affordance.
    recall_affordance = next(a for a in payload["affordance"] if a["command"] == "recall")
    assert recall_affordance["pct_resolvable"] == 50.0


def test_report_render_table_shows_each_section() -> None:
    """The human table names all three SLO sections and the suite scores."""
    table = _report().render_table()
    assert "Latency (ms)" in table
    assert "Fact-recall quality" in table
    assert "Affordance completeness" in table
    assert "synthetic" in table
    assert "search" in table


def _empty_store_report():
    probes = (_probe("recall", ("turn://t1",), payloads=("e1",)),)
    gt = [GroundTruthFact(entity="client-omega", attribute="primary-cloud", value="cloud-zeta")]
    suites = [("real-fact-store", gt, lambda _q: [], False)]
    return build_report(probes=probes, recall_suites=suites, concurrency_n=2, recall_k=5)


def test_render_table_shows_na_for_empty_store() -> None:
    """An empty-store recall suite renders ``N/A``, never a misleading ``0.000``.

    Sabotage-proof: drop the ``skipped`` branch in ``_render_recall_row`` →
    the row prints ``0.000`` and both the ``N/A`` and the ``0.000 not in``
    assertions fail. (Only the recall column uses 3-decimal formatting, so
    ``0.000`` is unique to a non-skipped recall score.)
    """
    table = _empty_store_report().render_table()
    assert "N/A" in table
    assert "0.000" not in table


def test_empty_store_report_json_recall_is_null() -> None:
    """`kairix slo --format json` surfaces null recall for an empty store."""
    payload = _empty_store_report().to_dict()
    assert payload["recall"][0]["skipped"] is True
    assert payload["recall"][0]["recall_at_k"] is None
