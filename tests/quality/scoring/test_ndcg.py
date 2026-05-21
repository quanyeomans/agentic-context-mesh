"""Unit tests for NDCGScorer.

Pins:

* happy-path: a high-relevance document at rank 1 → NDCG > 0.9.
* boundary: a relevance-0 (or omitted) document doesn't affect NDCG.
* empty inputs: empty retrieved set OR empty gold set → 0.0, never raise.
* error-path: ``QueryRunResult.error`` set → 0.0, no math.
* sabotage: shuffling the gold doc to rank 10 collapses NDCG below the
  rank-1 score.
"""

from __future__ import annotations

import pytest

from kairix.quality.scoring.ndcg import NDCGScorer
from kairix.quality.scoring.types import QueryRunResult

pytestmark = pytest.mark.unit


def _run(titles: tuple[str, ...]) -> QueryRunResult:
    return QueryRunResult(
        query_id="E-01",
        category="entity",
        query_text="Jordan Blake role",
        ranked_doc_titles=titles,
        ranked_doc_ids=tuple(f"id-{t}" for t in titles),
    )


class TestNDCGScorer:
    def test_happy_path_perfect_ranking(self) -> None:
        # Sabotage-proof: shuffle the gold doc to rank 5 and confirm score
        # drops; executed locally.
        scorer = NDCGScorer(
            gold_titles=[
                {"title": "jordan-blake", "relevance": 2},
                {"title": "team-overview", "relevance": 1},
            ],
            k=10,
        )
        run = _run(("jordan-blake", "team-overview", "noise"))
        result = scorer.score(run)
        assert result.metric_name == "ndcg_at_10"
        assert result.score == 1.0  # perfect ordering
        assert result.details["k"] == 10
        assert result.details["n_gold"] == 2
        assert result.details["n_retrieved"] == 3

    def test_relevance_zero_doc_does_not_move_score(self) -> None:
        # F-rule boundary: adding an irrelevant document after the gold
        # entries doesn't reduce NDCG (it just doesn't help).
        scorer = NDCGScorer(gold_titles=[{"title": "jordan-blake", "relevance": 2}], k=10)
        result_clean = scorer.score(_run(("jordan-blake",)))
        result_with_noise = scorer.score(_run(("jordan-blake", "unrelated-doc")))
        assert result_clean.score == result_with_noise.score == 1.0

    def test_empty_retrieved_returns_zero(self) -> None:
        scorer = NDCGScorer(gold_titles=[{"title": "jordan-blake", "relevance": 2}], k=10)
        result = scorer.score(_run(()))
        assert result.score == 0.0
        assert result.details["reason"] == "empty_retrieved"

    def test_missing_gold_returns_zero_gracefully(self) -> None:
        # Sabotage-proof: feeding the scorer a non-empty gold list and
        # confirming a non-zero score (e.g. the perfect-ranking test
        # above) is the partner mutation; this case pins the "no gold
        # supplied" path.
        scorer = NDCGScorer(gold_titles=None)
        result = scorer.score(_run(("any-doc",)))
        assert result.score == 0.0
        assert result.details["reason"] == "no_gold_titles"

    def test_error_path_returns_zero_with_reason(self) -> None:
        scorer = NDCGScorer(gold_titles=[{"title": "jordan-blake", "relevance": 2}])
        run = QueryRunResult(
            query_id="E-01",
            category="entity",
            query_text="q",
            error="backend timeout",
        )
        result = scorer.score(run)
        assert result.score == 0.0
        assert result.details["reason"] == "query_run_failed"
        assert result.details["error"] == "backend timeout"

    def test_demoting_gold_doc_collapses_score(self) -> None:
        # Executed sabotage: same suite, two retrieval shapes; the worse
        # ranking MUST score lower than the better. Mutation of the
        # NDCG math (e.g. removing the log2 discount) would let both
        # shapes score 1.0 and break this test.
        scorer = NDCGScorer(
            gold_titles=[{"title": "jordan-blake", "relevance": 2}],
            k=10,
        )
        ranked_first = scorer.score(_run(("jordan-blake", "a", "b", "c", "d", "e", "f", "g", "h", "i")))
        ranked_last = scorer.score(_run(("a", "b", "c", "d", "e", "f", "g", "h", "i", "jordan-blake")))
        assert ranked_first.score == 1.0
        assert ranked_last.score < 0.5  # heavily discounted at rank 10
        assert ranked_last.score < ranked_first.score

    def test_score_method_rejects_sequence_input(self) -> None:
        # Per-query scorer; aggregate use is invalid. Pins the contract.
        scorer = NDCGScorer(gold_titles=[{"title": "x", "relevance": 2}])
        result = scorer.score([_run(("x",)), _run(("y",))])
        assert result.score == 0.0
        assert "sequence" in result.details["reason"]

    def test_metric_name_override(self) -> None:
        scorer = NDCGScorer(
            gold_titles=[{"title": "x", "relevance": 2}],
            k=5,
            metric_name="ndcg_at_5",
        )
        result = scorer.score(_run(("x",)))
        assert result.metric_name == "ndcg_at_5"
        assert result.details["k"] == 5

    def test_name_property_is_stable(self) -> None:
        # Registry uses this. If it changes, downstream lookup breaks.
        assert NDCGScorer().name == "ndcg"
