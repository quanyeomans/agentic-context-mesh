"""
Tests for kairix.benchmark.dual_runner — verify the dual runner produces
baseline + comparison + deltas and detects regressions.
"""
from __future__ import annotations

import os

import pytest

from kairix.benchmark.dual_runner import DualBenchmarkResult, run_dual_benchmark


# Path to the reflib contract suite (relative to repo root)
_SUITE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "suites", "reflib-contract-suite.yaml"
)

# Path to the original contract suite
_CONTRACT_SUITE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "suites", "contract-suite.yaml"
)


@pytest.mark.unit
class TestDualRunnerBaselineOnly:
    """Dual runner with no comparison_db produces baseline only."""

    def test_baseline_only_returns_result(self) -> None:
        result = run_dual_benchmark(
            suite_path=_SUITE_PATH,
            baseline_db=None,
            comparison_db=None,
            system="mock-reflib",
        )
        assert isinstance(result, DualBenchmarkResult)
        assert result.baseline is not None
        assert result.comparison is None
        assert result.deltas == {}
        assert result.regression_detected is False

    def test_baseline_has_scores(self) -> None:
        result = run_dual_benchmark(
            suite_path=_SUITE_PATH,
            baseline_db=None,
            comparison_db=None,
            system="mock-reflib",
        )
        wt = result.baseline.summary["weighted_total"]
        assert wt > 0.0, f"Weighted total should be > 0, got {wt}"

    def test_baseline_has_category_scores(self) -> None:
        result = run_dual_benchmark(
            suite_path=_SUITE_PATH,
            baseline_db=None,
            comparison_db=None,
            system="mock-reflib",
        )
        cats = result.baseline.summary["category_scores"]
        assert "recall" in cats
        assert "entity" in cats
        assert "conceptual" in cats
        assert "procedural" in cats


@pytest.mark.unit
class TestDualRunnerWithComparison:
    """Dual runner with both baseline and comparison backends."""

    def test_comparison_produces_deltas(self) -> None:
        """Run both mock and mock-reflib against the contract suite to get deltas."""
        # Use the same mock-reflib system for both — deltas should be zero
        result = run_dual_benchmark(
            suite_path=_SUITE_PATH,
            baseline_db=None,
            comparison_db="dummy",  # comparison_db is not None, triggering comparison run
            system="mock-reflib",
        )
        assert result.comparison is not None
        assert "weighted_total" in result.deltas

        # Same system, same suite => deltas should be 0
        assert result.deltas["weighted_total"] == 0.0
        assert result.regression_detected is False

    def test_deltas_contain_all_categories(self) -> None:
        result = run_dual_benchmark(
            suite_path=_SUITE_PATH,
            baseline_db=None,
            comparison_db="dummy",
            system="mock-reflib",
        )
        for cat in ("recall", "entity", "conceptual", "procedural"):
            assert cat in result.deltas, f"Missing delta for {cat}"


@pytest.mark.unit
class TestDualRunnerRegressionDetection:
    """Verify regression detection logic."""

    def test_no_regression_when_same_system(self) -> None:
        result = run_dual_benchmark(
            suite_path=_SUITE_PATH,
            baseline_db=None,
            comparison_db="dummy",
            system="mock-reflib",
        )
        assert result.regression_detected is False

    def test_dataclass_fields(self) -> None:
        result = run_dual_benchmark(
            suite_path=_SUITE_PATH,
            baseline_db=None,
            comparison_db=None,
            system="mock-reflib",
        )
        # Verify DualBenchmarkResult has the expected fields
        assert hasattr(result, "baseline")
        assert hasattr(result, "comparison")
        assert hasattr(result, "deltas")
        assert hasattr(result, "regression_detected")
