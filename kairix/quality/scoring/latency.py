"""Latency scorer — post-hoc p50/p95/p99 across a batch of QueryRunResults.

Unlike NDCG / Hit / MRR / Judge which score one query at a time, the
latency scorer is an *aggregate* scorer: it consumes a ``Sequence[QueryRunResult]``
and emits one ``ScorerResult`` summarising the latency distribution.

The headline ``score`` value is the chosen percentile (default p95). The
``details`` dict carries the full LatencyStats fields (n, p50, p95, p99,
min, max, mean) for the reporter to render. A separate ``LatencyScorer``
can be instantiated per phase (cold / warm / load) to filter the input
before percentile calculation.

Math is delegated to :func:`kairix.quality.probe.stats.latency_stats`
— the same percentile implementation the existing PVT path uses, so
single-shot and concurrent-mode percentiles are directly comparable.

F26-clean: imports the latency math from
``kairix.quality.probe`` (a peer subpackage under ``quality/``), not
from any provider/transport. ``probe`` itself does not depend on this
module — there is no cycle.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from kairix.quality.probe.stats import latency_stats
from kairix.quality.scoring.types import (
    LatencyPhase,
    QueryRunResult,
    ScorerResult,
)

HeadlinePercentile = Literal["p50", "p95", "p99", "mean"]


class LatencyScorer:
    """Latency aggregate scorer.

    Constructor knobs:

    * ``headline`` — which percentile drives the ``score`` field.
      Defaults to ``"p95"`` matching `kairix.quality.probe.runner` and
      the published latency-gate convention.
    * ``phase`` — filter the input by ``QueryRunResult.latency_phase``.
      None (default) means "score across all phases". Set to ``"warm"``
      / ``"cold"`` / ``"load"`` to gate on one phase only.
    * ``metric_name`` — defaults to ``f"{headline}_ms"`` (e.g. ``"p95_ms"``).
    """

    def __init__(
        self,
        *,
        headline: HeadlinePercentile = "p95",
        phase: LatencyPhase | None = None,
        metric_name: str | None = None,
    ) -> None:
        self._headline = headline
        self._phase = phase
        self._metric_name = metric_name or f"{headline}_ms"

    @property
    def name(self) -> str:
        return "latency"

    def score(self, run: QueryRunResult | Sequence[QueryRunResult], /) -> ScorerResult:
        if isinstance(run, QueryRunResult):
            return self._score_sequence((run,))
        return self._score_sequence(tuple(run))

    def _score_sequence(self, runs: Sequence[QueryRunResult]) -> ScorerResult:
        eligible = [r for r in runs if r.error is None and (self._phase is None or r.latency_phase == self._phase)]
        latencies = [r.latency_ms for r in eligible]
        if not latencies:
            return ScorerResult(
                metric_name=self._metric_name,
                score=0.0,
                details={
                    "reason": "no_eligible_runs",
                    "n_total": len(runs),
                    "phase_filter": self._phase,
                },
            )
        stats = latency_stats(latencies)
        score_val = self._select_headline(stats)
        return ScorerResult(
            metric_name=self._metric_name,
            score=round(score_val, 1),
            details={
                "n": stats.n,
                "p50_ms": stats.p50_ms,
                "p95_ms": stats.p95_ms,
                "p99_ms": stats.p99_ms,
                "min_ms": stats.min_ms,
                "max_ms": stats.max_ms,
                "mean_ms": stats.mean_ms,
                "phase_filter": self._phase,
            },
        )

    def _select_headline(self, stats: object) -> float:
        # Pure dispatch — keeps F16 (cog complexity ≤15) trivially happy.
        return {
            "p50": getattr(stats, "p50_ms", 0.0),
            "p95": getattr(stats, "p95_ms", 0.0),
            "p99": getattr(stats, "p99_ms", 0.0),
            "mean": getattr(stats, "mean_ms", 0.0),
        }[self._headline]
