"""Per-category and overall aggregation of scorer results.

The benchmark reporter consumes:

* ``CategoryAggregate`` — for each category present in the run, the mean
  score across queries in that category, broken down per metric.
* ``OverallAggregate`` — the un-weighted mean across categories AND the
  category-weighted total (``CATEGORY_WEIGHTS`` from
  ``kairix.quality.eval.constants``), matching the behaviour the existing
  ``benchmark.runner.compute_weighted_total`` ships today.

This module is the single home for aggregation across the unified
benchmark surface. The legacy
``kairix.quality.benchmark.runner.aggregate_scores_by_category`` /
``compute_weighted_total`` / ``aggregate_ndcg_metrics`` helpers stay in
place for back-compat (P5 will collapse the reporter onto these
primitives); the new ``aggregate_by_category`` / ``aggregate_overall``
here are what the P3 mode dispatchers and the P5 reporter call against
the new ``QueryRunResult`` + ``ScorerResult`` pipeline.

F26-clean: imports only from ``kairix.quality.eval.constants`` (pure
data) and the scoring package's own types.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from kairix.quality.eval.constants import CATEGORY_WEIGHTS
from kairix.quality.scoring.types import QueryRunResult, ScorerResult


@dataclass(frozen=True)
class CategoryAggregate:
    """Aggregated metric scores for one category.

    ``metrics`` maps metric_name → mean across the category's queries.
    ``n`` is the number of queries seen for the category.
    """

    category: str
    n: int
    metrics: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class OverallAggregate:
    """Cross-category aggregation.

    ``unweighted`` is the equally-weighted mean of category means per
    metric. ``weighted`` applies ``CATEGORY_WEIGHTS`` — the same
    category-weighted total the legacy reporter prints (drives phase
    gates). ``per_category`` is the input list passed through for the
    reporter to render alongside.
    """

    per_category: tuple[CategoryAggregate, ...]
    unweighted: Mapping[str, float] = field(default_factory=dict)
    weighted: Mapping[str, float] = field(default_factory=dict)


def aggregate_by_category(
    pairs: Iterable[tuple[QueryRunResult, Iterable[ScorerResult]]],
) -> list[CategoryAggregate]:
    """Group ``(run, scorer_results)`` pairs by ``run.category`` and average.

    Each (run, results) pair is the output of running one query through
    the registered scorers — the typical shape is one ``QueryRunResult``
    fanned out to NDCG / Hit / MRR / Judge scorers per query.

    Returns one ``CategoryAggregate`` per distinct category, with
    ``metrics`` carrying the per-metric mean across that category's
    queries.
    """
    by_cat: dict[str, list[tuple[QueryRunResult, list[ScorerResult]]]] = defaultdict(list)
    for run, results in pairs:
        by_cat[run.category].append((run, list(results)))

    aggregates: list[CategoryAggregate] = []
    for cat in sorted(by_cat):
        cat_pairs = by_cat[cat]
        per_metric: dict[str, list[float]] = defaultdict(list)
        for _run, results in cat_pairs:
            for r in results:
                per_metric[r.metric_name].append(r.score)
        metrics = {name: round(sum(scores) / len(scores), 4) for name, scores in per_metric.items() if scores}
        aggregates.append(CategoryAggregate(category=cat, n=len(cat_pairs), metrics=metrics))
    return aggregates


def aggregate_overall(
    category_aggregates: Iterable[CategoryAggregate],
    *,
    weights: Mapping[str, float] | None = None,
) -> OverallAggregate:
    """Combine per-category aggregates into overall (unweighted + weighted) means.

    ``weights`` defaults to ``kairix.quality.eval.constants.CATEGORY_WEIGHTS``
    — the canonical weights driving phase-gate verdicts. Categories whose
    name is not in ``weights`` contribute 0 to the weighted total but are
    still included in the unweighted mean.
    """
    aggs = tuple(category_aggregates)
    effective_weights = dict(weights or CATEGORY_WEIGHTS)

    per_metric_unweighted: dict[str, list[float]] = defaultdict(list)
    per_metric_weighted: dict[str, float] = defaultdict(float)
    for agg in aggs:
        for metric, value in agg.metrics.items():
            per_metric_unweighted[metric].append(value)
            per_metric_weighted[metric] += value * effective_weights.get(agg.category, 0.0)

    unweighted = {m: round(sum(vals) / len(vals), 4) for m, vals in per_metric_unweighted.items() if vals}
    weighted = {m: round(v, 4) for m, v in per_metric_weighted.items()}
    return OverallAggregate(per_category=aggs, unweighted=unweighted, weighted=weighted)
