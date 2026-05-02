"""Unit tests for BenchmarkPipeline orchestrator."""

from __future__ import annotations

import pytest

from kairix.quality.benchmark.pipeline import BenchmarkPipeline
from kairix.quality.benchmark.suite import BenchmarkCase, BenchmarkSuite


def _make_mock_suite() -> BenchmarkSuite:
    """Build a minimal BenchmarkSuite with mock-backend cases."""
    return BenchmarkSuite(
        meta={"name": "test-pipeline", "version": "1.0"},
        cases=[
            BenchmarkCase(
                id="t1",
                category="recall",
                query="test query",
                gold_path="docs/test.md",
                score_method="exact",
            ),
        ],
    )


@pytest.mark.unit
class TestBenchmarkPipeline:
    def test_run_returns_benchmark_result(self) -> None:
        """Pipeline.run returns a BenchmarkResult with expected structure."""
        from kairix.quality.benchmark.runner import BenchmarkResult

        pipeline = BenchmarkPipeline(system="mock")
        suite = _make_mock_suite()
        result = pipeline.run(suite)

        assert isinstance(result, BenchmarkResult)
        assert "weighted_total" in result.summary
        assert "category_scores" in result.summary
        assert len(result.cases) == 1

    def test_run_uses_configured_system(self) -> None:
        """Pipeline passes the configured system through to runner."""
        pipeline = BenchmarkPipeline(system="mock")
        suite = _make_mock_suite()
        result = pipeline.run(suite)

        assert result.meta["system"] == "mock"

    def test_run_propagates_agent(self) -> None:
        """Pipeline passes agent to the runner."""
        pipeline = BenchmarkPipeline(system="mock", agent="builder")
        suite = _make_mock_suite()
        result = pipeline.run(suite)

        assert result.meta["agent"] == "builder"
