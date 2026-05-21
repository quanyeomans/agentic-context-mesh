"""Unit tests for MRRScorer.

Pins:

* happy-path rank-1: MRR = 1.0.
* happy-path rank-3: MRR = 1/3 ≈ 0.333.
* boundary: gold at rank K+1 → 0.0 (outside cutoff).
* empty / no-gold / error → 0.0.
* sabotage: gold doc moves from rank 1 to rank 4 → 1.0 → 0.25.
"""

from __future__ import annotations

import pytest

from kairix.quality.scoring.mrr import MRRScorer
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


class TestMRRScorer:
    def test_gold_at_rank_1_is_one(self) -> None:
        scorer = MRRScorer(gold_titles=[{"title": "x", "relevance": 2}])
        assert scorer.score(_run(("x", "y"))).score == 1.0

    def test_gold_at_rank_3_is_one_third(self) -> None:
        scorer = MRRScorer(gold_titles=[{"title": "x", "relevance": 1}])
        result = scorer.score(_run(("a", "b", "x")))
        assert result.score == pytest.approx(1.0 / 3.0, abs=1e-4)

    def test_gold_outside_k_returns_zero(self) -> None:
        # Sabotage-proof: same scorer with K=5, gold at rank 6 → 0.0.
        scorer = MRRScorer(gold_titles=[{"title": "x", "relevance": 1}], k=5)
        result = scorer.score(_run(("a", "b", "c", "d", "e", "x")))
        assert result.score == 0.0

    def test_demotion_collapses_score(self) -> None:
        # Executed sabotage: moving the gold doc from rank 1 to rank 4
        # forces the score from 1.0 to 0.25. Mutation of the math (e.g.
        # forgetting the 1/(i+1) discount) would let both shapes return
        # 1.0 and break this test.
        scorer = MRRScorer(gold_titles=[{"title": "x", "relevance": 1}])
        front = scorer.score(_run(("x", "a", "b", "c"))).score
        back = scorer.score(_run(("a", "b", "c", "x"))).score
        assert front == 1.0
        assert back == pytest.approx(0.25, abs=1e-4)
        assert back < front

    def test_empty_retrieved_returns_zero(self) -> None:
        scorer = MRRScorer(gold_titles=[{"title": "x", "relevance": 2}])
        result = scorer.score(_run(()))
        assert result.score == 0.0
        assert result.details["reason"] == "empty_retrieved"

    def test_missing_gold_returns_zero(self) -> None:
        scorer = MRRScorer(gold_titles=None)
        result = scorer.score(_run(("a",)))
        assert result.score == 0.0
        assert result.details["reason"] == "no_gold_titles"

    def test_error_path_returns_zero(self) -> None:
        scorer = MRRScorer(gold_titles=[{"title": "x", "relevance": 1}])
        run = QueryRunResult(query_id="x", category="entity", query_text="q", error="oops")
        result = scorer.score(run)
        assert result.score == 0.0
        assert result.details["reason"] == "query_run_failed"

    def test_sequence_input_rejected(self) -> None:
        scorer = MRRScorer(gold_titles=[{"title": "x", "relevance": 2}])
        result = scorer.score([_run(("x",))])
        assert result.score == 0.0

    def test_name_property(self) -> None:
        assert MRRScorer().name == "mrr"

    def test_relevance_zero_gold_does_not_count(self) -> None:
        scorer = MRRScorer(gold_titles=[{"title": "x", "relevance": 0}])
        result = scorer.score(_run(("x",)))
        assert result.score == 0.0
