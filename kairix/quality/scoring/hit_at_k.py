"""Hit@K scorer — binary "any-relevant-in-top-k" hit rate.

Wraps :func:`kairix.quality.eval.metrics.hit_at_k_graded` into the Scorer
Protocol. Returns 1.0 when at least one document in the top-K positions
of ``QueryRunResult.ranked_doc_titles`` matches a gold entry with
relevance ≥ 1; 0.0 otherwise.

K defaults to 5 (matches the Hit@5 reported in
``docs/evaluation/EVALUATION.md``).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from kairix.quality.eval.metrics import hit_at_k_graded
from kairix.quality.scoring.types import (
    QueryRunResult,
    ScorerResult,
)


class HitAtKScorer:
    """Hit@K — binary "any-relevant-in-top-k" hit rate."""

    def __init__(
        self,
        *,
        gold_titles: list[dict[str, Any]] | None = None,
        k: int = 5,
        metric_name: str = "hit_at_5",
    ) -> None:
        self._gold = list(gold_titles or [])
        self._k = k
        self._metric_name = metric_name

    @property
    def name(self) -> str:
        return "hit_at_k"

    def score(self, run: QueryRunResult | Sequence[QueryRunResult], /) -> ScorerResult:
        if not isinstance(run, QueryRunResult):
            return ScorerResult(
                metric_name=self._metric_name,
                score=0.0,
                details={"reason": "hit_at_k scorer received a sequence; expects one QueryRunResult"},
            )
        if run.error:
            return ScorerResult(
                metric_name=self._metric_name,
                score=0.0,
                details={"reason": "query_run_failed", "error": run.error},
            )
        if not self._gold:
            return ScorerResult(
                metric_name=self._metric_name,
                score=0.0,
                details={"reason": "no_gold_titles"},
            )
        retrieved = list(run.ranked_doc_titles)
        if not retrieved:
            return ScorerResult(
                metric_name=self._metric_name,
                score=0.0,
                details={"reason": "empty_retrieved"},
            )
        hit = hit_at_k_graded(retrieved, self._gold, k=self._k)
        return ScorerResult(
            metric_name=self._metric_name,
            score=1.0 if hit else 0.0,
            details={"k": self._k, "n_gold": len(self._gold), "n_retrieved": len(retrieved)},
        )
