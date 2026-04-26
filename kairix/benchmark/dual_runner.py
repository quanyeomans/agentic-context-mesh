"""
Dual-corpus benchmark runner for kairix.

Runs a benchmark suite against two retrieval backends (baseline and comparison)
and computes per-category score deltas. Used to compare a reference library
against a user's knowledge store, or to measure the impact of a system change.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from kairix.benchmark.runner import BenchmarkResult, run_benchmark
from kairix.benchmark.suite import load_suite


@dataclass
class DualBenchmarkResult:
    """Result of running the same suite against two retrieval backends."""

    baseline: BenchmarkResult
    comparison: BenchmarkResult | None
    deltas: dict[str, float] = field(default_factory=dict)
    regression_detected: bool = False


# Regression threshold: comparison score drop > this triggers a flag
_REGRESSION_THRESHOLD = 0.02


def run_dual_benchmark(
    suite_path: str,
    baseline_db: str | None = None,
    comparison_db: str | None = None,
    system: str = "hybrid",
) -> DualBenchmarkResult:
    """
    Run a benchmark suite against a baseline and (optionally) a comparison backend.

    Args:
        suite_path:     Path to the YAML suite file.
        baseline_db:    Database path for the baseline run. None uses the default.
        comparison_db:  Database path for the comparison run. None skips comparison.
        system:         Retrieval system name (e.g. 'hybrid', 'mock', 'mock-reflib').

    Returns:
        DualBenchmarkResult with baseline, optional comparison, deltas, and
        regression flag.
    """
    suite = load_suite(suite_path)

    # Run baseline
    baseline = run_benchmark(suite, system=system, db_path=baseline_db)

    # Run comparison (if a comparison_db is provided)
    comparison: BenchmarkResult | None = None
    deltas: dict[str, float] = {}
    regression_detected = False

    if comparison_db is not None:
        comparison = run_benchmark(suite, system=system, db_path=comparison_db)

        # Compute deltas: comparison - baseline (positive = comparison is better)
        baseline_cats = baseline.summary["category_scores"]
        comparison_cats = comparison.summary["category_scores"]
        all_cats = set(baseline_cats.keys()) | set(comparison_cats.keys())
        for cat in sorted(all_cats):
            b_score = baseline_cats.get(cat, 0.0)
            c_score = comparison_cats.get(cat, 0.0)
            deltas[cat] = round(c_score - b_score, 4)

        # Overall weighted total delta
        deltas["weighted_total"] = round(
            comparison.summary["weighted_total"] - baseline.summary["weighted_total"],
            4,
        )

        # Regression detected if comparison weighted total drops below baseline by threshold
        regression_detected = deltas["weighted_total"] < -_REGRESSION_THRESHOLD

    return DualBenchmarkResult(
        baseline=baseline,
        comparison=comparison,
        deltas=deltas,
        regression_detected=regression_detected,
    )
