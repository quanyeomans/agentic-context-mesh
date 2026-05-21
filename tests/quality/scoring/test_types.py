"""Unit tests pinning the shared scorer types.

`QueryRunResult` is the wire format between the mode dispatcher (P3)
and the scorers (P2) — its shape is a hard contract. These tests pin
the dataclass fields, default values, and frozen invariant.

`Scorer` is a runtime-checkable Protocol; we assert that a minimal
concrete class is recognised by ``isinstance``.

Sabotage convention. Each test names the mutation that should break
it (e.g. "drop a required field → TypeError"). Executed at PR time.
"""

from __future__ import annotations

import dataclasses

import pytest

from kairix.quality.scoring.types import (
    QueryRunResult,
    Scorer,
    ScorerResult,
)

pytestmark = pytest.mark.unit


class TestQueryRunResult:
    """Pin the QueryRunResult contract — fields, defaults, frozen-ness."""

    def test_construct_with_required_fields_only(self) -> None:
        # Sabotage-proof: rename query_id → query_identifier in types.py,
        # rerun → TypeError. Executed locally; documented here.
        run = QueryRunResult(
            query_id="E-01",
            category="entity",
            query_text="Jordan Blake role",
        )
        assert run.query_id == "E-01"
        assert run.category == "entity"
        assert run.query_text == "Jordan Blake role"

    def test_default_ranked_lists_are_empty_tuples(self) -> None:
        # Sabotage-proof: change default to list, rerun → frozen violation
        # would propagate (lists are mutable; tuples are not).
        run = QueryRunResult(query_id="x", category="recall", query_text="q")
        assert run.ranked_doc_ids == ()
        assert run.ranked_doc_titles == ()
        assert isinstance(run.ranked_doc_ids, tuple)
        assert isinstance(run.ranked_doc_titles, tuple)

    def test_default_synthesised_answer_is_none(self) -> None:
        run = QueryRunResult(query_id="x", category="recall", query_text="q")
        assert run.synthesised_answer is None

    def test_default_latency_phase_is_warm(self) -> None:
        run = QueryRunResult(query_id="x", category="recall", query_text="q")
        assert run.latency_phase == "warm"
        assert run.latency_ms == 0.0

    def test_default_error_is_none(self) -> None:
        run = QueryRunResult(query_id="x", category="recall", query_text="q")
        assert run.error is None

    def test_construct_with_full_payload(self) -> None:
        run = QueryRunResult(
            query_id="E-01",
            category="entity",
            query_text="Jordan Blake role",
            ranked_doc_ids=("d1", "d2"),
            ranked_doc_titles=("jordan-blake", "team-overview"),
            synthesised_answer="Jordan is the lead engineer.",
            latency_ms=42.5,
            latency_phase="cold",
            error=None,
        )
        assert run.ranked_doc_ids == ("d1", "d2")
        assert run.ranked_doc_titles == ("jordan-blake", "team-overview")
        assert run.synthesised_answer == "Jordan is the lead engineer."
        assert run.latency_ms == 42.5
        assert run.latency_phase == "cold"

    def test_frozen_dataclass_rejects_mutation(self) -> None:
        # Sabotage-proof: remove `frozen=True` from the dataclass
        # decorator, rerun → this passes (no FrozenInstanceError), fails
        # the invariant.
        run = QueryRunResult(query_id="x", category="recall", query_text="q")
        with pytest.raises(dataclasses.FrozenInstanceError):
            run.query_id = "y"  # type: ignore[misc]


class TestScorerResult:
    """Pin ScorerResult shape — metric_name, score, details."""

    def test_construct_minimal(self) -> None:
        result = ScorerResult(metric_name="ndcg_at_10", score=0.85)
        assert result.metric_name == "ndcg_at_10"
        assert result.score == 0.85
        assert result.details == {}

    def test_details_default_is_empty_dict(self) -> None:
        result = ScorerResult(metric_name="hit_at_5", score=1.0)
        assert isinstance(result.details, dict)
        assert result.details == {}

    def test_details_payload_round_trip(self) -> None:
        result = ScorerResult(
            metric_name="ndcg_at_10",
            score=0.5,
            details={"per_rank_relevance": [2, 1, 0]},
        )
        assert result.details["per_rank_relevance"] == [2, 1, 0]


class TestScorerProtocol:
    """Pin the Scorer Protocol — runtime-checkable, structural."""

    def test_minimal_implementation_satisfies_protocol(self) -> None:
        # Sabotage-proof: remove `@runtime_checkable` from the Protocol
        # decorator, rerun → TypeError on isinstance.

        class _DummyScorer:
            @property
            def name(self) -> str:
                return "dummy"

            def score(self, run: object, /) -> ScorerResult:
                return ScorerResult(metric_name="dummy", score=1.0)

        scorer = _DummyScorer()
        assert isinstance(scorer, Scorer)

    def test_object_missing_score_method_does_not_satisfy(self) -> None:
        # Sabotage-proof: add a `score` attribute (not method) to the
        # dummy class — Protocol still checks attribute presence at
        # runtime, so this test pins "no score attr → not a Scorer".

        class _NoScore:
            @property
            def name(self) -> str:
                return "broken"

        scorer = _NoScore()
        assert not isinstance(scorer, Scorer)
