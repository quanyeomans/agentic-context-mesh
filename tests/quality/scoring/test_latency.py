"""Unit tests for LatencyScorer.

Pins:

* happy-path: percentile computation matches probe.stats.latency_stats
  for the same input → no math drift between PVT and unified benchmark.
* single QueryRunResult input: treated as a 1-element sequence.
* phase filter: ``phase="warm"`` excludes cold/load runs.
* error path: runs with ``error`` populated are excluded from the
  sample; scorer never raises.
* empty input: returns 0.0 with ``details["reason"] = "no_eligible_runs"``.
* headline selection: ``headline="p50"`` returns p50, ``"p99"`` returns
  p99 — sabotage proof by varying headlines on the same input.
"""

from __future__ import annotations

import pytest

from kairix.quality.probe.stats import latency_stats
from kairix.quality.scoring.latency import LatencyScorer
from kairix.quality.scoring.types import LatencyPhase, QueryRunResult

pytestmark = pytest.mark.unit


def _run(latency_ms: float, *, phase: LatencyPhase = "warm", error: str | None = None) -> QueryRunResult:
    return QueryRunResult(
        query_id=f"L-{int(latency_ms)}",
        category="recall",
        query_text="q",
        latency_ms=latency_ms,
        latency_phase=phase,
        error=error,
    )


class TestLatencyScorer:
    def test_percentile_matches_probe_stats(self) -> None:
        # Direct sanity: feeding the same latencies to LatencyScorer and
        # to probe.stats.latency_stats yields the same p95. If the math
        # drifts (mutation: change rank rounding in latency_stats) both
        # would shift together — but this contract pins that the unified
        # benchmark uses the SAME numbers PVT reports.
        latencies = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
        runs = [_run(latency) for latency in latencies]
        scorer = LatencyScorer(headline="p95")
        result = scorer.score(runs)
        expected = latency_stats(latencies).p95_ms
        assert result.score == expected
        assert result.details["p95_ms"] == expected

    def test_single_run_passed_as_value_works(self) -> None:
        # Aggregate scorer should still accept a single QueryRunResult
        # (per the Protocol's union type).
        scorer = LatencyScorer(headline="p50")
        result = scorer.score(_run(42.0))
        assert result.score == 42.0
        assert result.details["n"] == 1

    def test_empty_returns_zero_with_reason(self) -> None:
        scorer = LatencyScorer(headline="p95")
        result = scorer.score([])
        assert result.score == 0.0
        assert result.details["reason"] == "no_eligible_runs"
        assert result.details["n_total"] == 0

    def test_phase_filter_excludes_other_phases(self) -> None:
        # Sabotage-proof: change phase="warm" → phase=None on the scorer
        # and the score includes the cold + load latencies, shifting it.
        # Executed via the partner test below ("test_phase_none_includes_all").
        scorer = LatencyScorer(headline="p50", phase="warm")
        runs = [
            _run(10.0, phase="warm"),
            _run(20.0, phase="warm"),
            _run(1000.0, phase="cold"),  # excluded
            _run(2000.0, phase="load"),  # excluded
        ]
        result = scorer.score(runs)
        assert result.details["n"] == 2
        assert result.score in (10.0, 20.0)  # p50 of [10, 20]

    def test_phase_none_includes_all(self) -> None:
        scorer = LatencyScorer(headline="p99", phase=None)
        runs = [_run(10.0, phase="warm"), _run(2000.0, phase="cold")]
        result = scorer.score(runs)
        assert result.details["n"] == 2

    def test_error_runs_excluded(self) -> None:
        scorer = LatencyScorer(headline="p50")
        runs = [
            _run(50.0),
            _run(9999.0, error="backend timeout"),  # excluded
            _run(60.0),
        ]
        result = scorer.score(runs)
        assert result.details["n"] == 2
        assert result.score in (50.0, 60.0)  # not 9999

    def test_headline_p50_vs_p99_differs(self) -> None:
        # Executed sabotage: changing the headline switches the headline
        # number; if it didn't, this test fails.
        runs = [_run(latency) for latency in [10.0, 10.0, 10.0, 10.0, 10.0, 1000.0]]
        p50 = LatencyScorer(headline="p50").score(runs)
        p99 = LatencyScorer(headline="p99").score(runs)
        assert p50.score < p99.score

    def test_headline_p95_default_metric_name(self) -> None:
        scorer = LatencyScorer()
        result = scorer.score([_run(50.0)])
        assert result.metric_name == "p95_ms"

    def test_custom_metric_name(self) -> None:
        scorer = LatencyScorer(headline="p99", metric_name="tail_latency_ms")
        result = scorer.score([_run(50.0)])
        assert result.metric_name == "tail_latency_ms"

    def test_name_property(self) -> None:
        assert LatencyScorer().name == "latency"

    def test_details_carries_full_stats(self) -> None:
        # Reporter needs all six stats fields; pin the contract.
        runs = [_run(latency) for latency in [10.0, 20.0, 30.0]]
        result = LatencyScorer(headline="p50").score(runs)
        for key in ("n", "p50_ms", "p95_ms", "p99_ms", "min_ms", "max_ms", "mean_ms", "phase_filter"):
            assert key in result.details

    def test_mean_headline(self) -> None:
        runs = [_run(latency) for latency in [10.0, 20.0, 30.0]]
        result = LatencyScorer(headline="mean").score(runs)
        assert result.score == pytest.approx(20.0, abs=1e-1)
