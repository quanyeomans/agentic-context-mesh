"""MRR@k scorer — Mean Reciprocal Rank of the first relevant document.

Wraps :func:`kairix.quality.eval.metrics.reciprocal_rank_graded` into the
Scorer Protocol. Returns ``1/rank`` of the first relevant document
(rank ≥ 1) in the top-K positions of ``QueryRunResult.ranked_doc_titles``,
or 0.0 when no relevant document appears in the top-K.

K defaults to 10 (matches the MRR@10 reported in
``docs/evaluation/EVALUATION.md``).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from kairix.quality.eval.metrics import reciprocal_rank_graded
from kairix.quality.scoring.types import (
    QueryRunResult,
    ScorerResult,
)


class MRRScorer:
    """MRR@k — reciprocal rank of the first relevant document."""

    def __init__(
        self,
        *,
        gold_titles: list[dict[str, Any]] | None = None,
        k: int = 10,
        metric_name: str = "mrr_at_10",
    ) -> None:
        self._gold = list(gold_titles or [])
        self._k = k
        self._metric_name = metric_name

    @property
    def name(self) -> str:
        return "mrr"

    def score(self, run: QueryRunResult | Sequence[QueryRunResult], /) -> ScorerResult:
        if not isinstance(run, QueryRunResult):
            return ScorerResult(
                metric_name=self._metric_name,
                score=0.0,
                details={"reason": "mrr scorer received a sequence; expects one QueryRunResult"},
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
        rr = reciprocal_rank_graded(retrieved, self._gold, k=self._k)
        return ScorerResult(
            metric_name=self._metric_name,
            score=round(rr, 4),
            details={"k": self._k, "n_gold": len(self._gold), "n_retrieved": len(retrieved)},
        )
