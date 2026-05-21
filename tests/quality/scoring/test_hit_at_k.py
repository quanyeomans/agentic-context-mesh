"""Unit tests for HitAtKScorer.

Pins:

* happy-path: a gold document at rank 1 → 1.0.
* rank cutoff: a gold document at rank K+1 → 0.0 (drops below floor).
* empty inputs: empty retrieved or empty gold → 0.0 gracefully.
* error path: ``run.error`` set → 0.0.
* sabotage: gold doc moves from rank 5 to rank 6 with K=5 → score
  collapses from 1.0 to 0.0.
"""

from __future__ import annotations

import pytest

from kairix.quality.scoring.hit_at_k import HitAtKScorer
from kairix.quality.scoring.types import QueryRunResult

pytestmark = pytest.mark.unit


def _run(titles: tuple[str, ...]) -> QueryRunResult:
    return QueryRunResult(
        query_id="E-01",
        category="entity",
        query_text="q",
        ranked_doc_titles=titles,
        ranked_doc_ids=tuple(f"id-{t}" for t in titles),
    )


class TestHitAtKScorer:
    def test_gold_at_rank_1_is_hit(self) -> None:
        scorer = HitAtKScorer(gold_titles=[{"title": "jordan-blake", "relevance": 2}], k=5)
        result = scorer.score(_run(("jordan-blake", "noise")))
        assert result.score == 1.0
        assert result.metric_name == "hit_at_5"

    def test_gold_at_rank_5_with_k5_is_hit(self) -> None:
        scorer = HitAtKScorer(gold_titles=[{"title": "target", "relevance": 1}], k=5)
        run = _run(("a", "b", "c", "d", "target"))
        assert scorer.score(run).score == 1.0

    def test_gold_at_rank_6_with_k5_is_miss(self) -> None:
        # Sabotage-proof: same scorer, gold moved one position later;
        # 1.0 → 0.0. Executed via this delta test directly.
        scorer = HitAtKScorer(gold_titles=[{"title": "target", "relevance": 1}], k=5)
        run = _run(("a", "b", "c", "d", "e", "target"))
        assert scorer.score(run).score == 0.0

    def test_empty_retrieved_returns_zero(self) -> None:
        scorer = HitAtKScorer(gold_titles=[{"title": "x", "relevance": 1}])
        result = scorer.score(_run(()))
        assert result.score == 0.0
        assert result.details["reason"] == "empty_retrieved"

    def test_missing_gold_returns_zero(self) -> None:
        scorer = HitAtKScorer(gold_titles=None)
        result = scorer.score(_run(("x",)))
        assert result.score == 0.0
        assert result.details["reason"] == "no_gold_titles"

    def test_relevance_zero_gold_does_not_count_as_hit(self) -> None:
        # Boundary: gold entry with relevance 0 is "explicitly not
        # relevant" — should NOT trip Hit@K.
        scorer = HitAtKScorer(gold_titles=[{"title": "neutral", "relevance": 0}], k=5)
        result = scorer.score(_run(("neutral",)))
        assert result.score == 0.0

    def test_error_path_returns_zero(self) -> None:
        scorer = HitAtKScorer(gold_titles=[{"title": "x", "relevance": 1}])
        run = QueryRunResult(query_id="x", category="entity", query_text="q", error="oops")
        result = scorer.score(run)
        assert result.score == 0.0
        assert result.details["reason"] == "query_run_failed"

    def test_sequence_input_rejected(self) -> None:
        scorer = HitAtKScorer(gold_titles=[{"title": "x", "relevance": 2}])
        result = scorer.score([_run(("x",))])
        assert result.score == 0.0
        assert "sequence" in result.details["reason"]

    def test_name_property(self) -> None:
        assert HitAtKScorer().name == "hit_at_k"

    def test_custom_k_and_metric_name(self) -> None:
        scorer = HitAtKScorer(
            gold_titles=[{"title": "x", "relevance": 1}],
            k=3,
            metric_name="hit_at_3",
        )
        result = scorer.score(_run(("a", "b", "x")))
        assert result.score == 1.0
        assert result.metric_name == "hit_at_3"
        assert result.details["k"] == 3
