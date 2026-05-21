"""Unit tests for the per-category / overall aggregator.

Pins:

* per-category: mean of per-query scores, grouped by category.
* overall unweighted: equal-weight mean across categories.
* overall weighted: applies CATEGORY_WEIGHTS — matches the legacy
  benchmark.runner.compute_weighted_total behaviour.
* empty input: returns empty aggregates without raising.
* sabotage: dropping a category's queries from the input drops it from
  the aggregate; mutating CATEGORY_WEIGHTS would shift the weighted total.
"""

from __future__ import annotations

import pytest

from kairix.quality.scoring.aggregator import (
    CategoryAggregate,
    aggregate_by_category,
    aggregate_overall,
)
from kairix.quality.scoring.types import QueryRunResult, ScorerResult

pytestmark = pytest.mark.unit


def _run(qid: str, category: str) -> QueryRunResult:
    return QueryRunResult(query_id=qid, category=category, query_text="q")


def _score(name: str, value: float) -> ScorerResult:
    return ScorerResult(metric_name=name, score=value)


class TestAggregateByCategory:
    def test_groups_runs_by_category(self) -> None:
        # Sabotage-proof: drop the "entity" pair → entity disappears from
        # the aggregate list; assertion below would fail.
        pairs = [
            (_run("e-1", "entity"), [_score("ndcg_at_10", 0.8)]),
            (_run("e-2", "entity"), [_score("ndcg_at_10", 0.6)]),
            (_run("r-1", "recall"), [_score("ndcg_at_10", 1.0)]),
        ]
        aggregates = aggregate_by_category(pairs)
        by_cat = {a.category: a for a in aggregates}
        assert set(by_cat) == {"entity", "recall"}
        assert by_cat["entity"].n == 2
        assert by_cat["recall"].n == 1
        assert by_cat["entity"].metrics["ndcg_at_10"] == pytest.approx(0.7, abs=1e-4)
        assert by_cat["recall"].metrics["ndcg_at_10"] == pytest.approx(1.0, abs=1e-4)

    def test_handles_multiple_metrics_per_run(self) -> None:
        pairs = [
            (_run("e-1", "entity"), [_score("ndcg_at_10", 0.8), _score("hit_at_5", 1.0)]),
            (_run("e-2", "entity"), [_score("ndcg_at_10", 0.4), _score("hit_at_5", 0.0)]),
        ]
        [aggregate] = aggregate_by_category(pairs)
        assert aggregate.category == "entity"
        assert aggregate.metrics["ndcg_at_10"] == pytest.approx(0.6, abs=1e-4)
        assert aggregate.metrics["hit_at_5"] == pytest.approx(0.5, abs=1e-4)

    def test_empty_input_returns_empty_list(self) -> None:
        assert aggregate_by_category([]) == []

    def test_drops_metrics_with_no_observations(self) -> None:
        # Boundary: a category aggregate skips metric_names that
        # weren't produced by any query.
        pairs = [(_run("e-1", "entity"), [_score("ndcg_at_10", 0.5)])]
        [agg] = aggregate_by_category(pairs)
        assert "hit_at_5" not in agg.metrics
        assert agg.metrics == {"ndcg_at_10": 0.5}


class TestAggregateOverall:
    def test_unweighted_is_mean_of_category_means(self) -> None:
        aggs = [
            CategoryAggregate(category="entity", n=2, metrics={"ndcg_at_10": 0.6}),
            CategoryAggregate(category="recall", n=1, metrics={"ndcg_at_10": 1.0}),
        ]
        overall = aggregate_overall(aggs)
        assert overall.unweighted["ndcg_at_10"] == pytest.approx(0.8, abs=1e-4)

    def test_weighted_applies_default_category_weights(self) -> None:
        # Default CATEGORY_WEIGHTS gives recall=0.25, entity=0.20.
        # Sabotage-proof: pass weights={"recall": 1.0, "entity": 0.0}
        # → the weighted ndcg drops to recall's value (1.0); below
        # confirms the default behaviour.
        aggs = [
            CategoryAggregate(category="entity", n=2, metrics={"ndcg_at_10": 0.6}),
            CategoryAggregate(category="recall", n=1, metrics={"ndcg_at_10": 1.0}),
        ]
        overall = aggregate_overall(aggs)
        # weighted = entity*0.20 + recall*0.25 = 0.12 + 0.25 = 0.37
        assert overall.weighted["ndcg_at_10"] == pytest.approx(0.37, abs=1e-4)

    def test_weighted_accepts_custom_weights(self) -> None:
        aggs = [
            CategoryAggregate(category="entity", n=2, metrics={"ndcg_at_10": 0.6}),
            CategoryAggregate(category="recall", n=1, metrics={"ndcg_at_10": 1.0}),
        ]
        overall = aggregate_overall(aggs, weights={"entity": 0.5, "recall": 0.5})
        assert overall.weighted["ndcg_at_10"] == pytest.approx(0.8, abs=1e-4)

    def test_categories_not_in_weights_contribute_zero_to_weighted(self) -> None:
        # Sabotage: an unknown category like "freeform" shouldn't blow
        # up the weighted aggregate, just contribute nothing.
        aggs = [
            CategoryAggregate(category="freeform", n=1, metrics={"ndcg_at_10": 1.0}),
        ]
        overall = aggregate_overall(aggs)
        assert overall.weighted["ndcg_at_10"] == 0.0
        assert overall.unweighted["ndcg_at_10"] == 1.0

    def test_empty_aggregates_round_trip_cleanly(self) -> None:
        overall = aggregate_overall([])
        assert overall.per_category == ()
        assert overall.unweighted == {}
        assert overall.weighted == {}
