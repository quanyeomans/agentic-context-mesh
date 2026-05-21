"""NDCG@k scorer with graded relevance.

Wraps :func:`kairix.quality.eval.metrics.ndcg_graded` — the single source of
truth for the NDCG math — into the ``Scorer`` Protocol surface that
consumes a ``QueryRunResult``.

NDCG@k math (per ``docs/evaluation/EVALUATION.md`` §Metrics):

* Discounted Cumulative Gain (DCG) for a ranked list of relevances:
  ``sum(rel_i / log2(i + 2))`` over the top-k positions.
* Ideal DCG (IDCG): same calculation against the gold list sorted by
  relevance descending.
* NDCG = DCG / IDCG when IDCG > 0, else 0.0.

Relevance scale matches the suite YAML — 2 = directly answers, 1 =
partially relevant, 0 = irrelevant (implicit for any document not
listed in ``gold_titles``).

Empty-input handling: when ``QueryRunResult.ranked_doc_titles`` is empty
OR when the configured ``gold_titles`` is empty/None, the scorer returns
0.0 with ``details["reason"]`` explaining why — never raises.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from kairix.quality.eval.metrics import ndcg_graded
from kairix.quality.scoring.types import (
    QueryRunResult,
    ScorerResult,
)


class NDCGScorer:
    """NDCG@k with graded relevance.

    Constructor takes ``gold_titles`` (list of ``{"title": ..., "relevance": N}``
    dicts) and ``k`` (cut-off rank, default 10 per
    ``docs/evaluation/EVALUATION.md``). The same scorer instance can be
    reused across many ``QueryRunResult``s when the gold is invariant
    (one gold per query in the typical benchmark; one scorer per query
    in the pluggable-registry design).
    """

    def __init__(
        self,
        *,
        gold_titles: list[dict[str, Any]] | None = None,
        k: int = 10,
        metric_name: str = "ndcg_at_10",
    ) -> None:
        self._gold = list(gold_titles or [])
        self._k = k
        self._metric_name = metric_name

    @property
    def name(self) -> str:
        return "ndcg"

    def score(self, run: QueryRunResult | Sequence[QueryRunResult], /) -> ScorerResult:
        if not isinstance(run, QueryRunResult):
            return ScorerResult(
                metric_name=self._metric_name,
                score=0.0,
                details={
                    "reason": "ndcg scorer received a sequence; per-query scorer expects one QueryRunResult",
                },
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
        score = ndcg_graded(retrieved, self._gold, k=self._k)
        return ScorerResult(
            metric_name=self._metric_name,
            score=round(score, 4),
            details={
                "k": self._k,
                "n_gold": len(self._gold),
                "n_retrieved": len(retrieved),
            },
        )
