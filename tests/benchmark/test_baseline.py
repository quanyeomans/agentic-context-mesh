"""Tests for benchmark baseline comparison module."""

from __future__ import annotations

from pathlib import Path

import pytest

from kairix.benchmark.baseline import CATEGORY_FLOOR, compare


def _make_result(weighted_total: float, category_scores: dict | None = None) -> dict:
    return {
        "meta": {"weighted_total": weighted_total},
        "summary": {
            "weighted_total": weighted_total,
            "category_scores": category_scores
            or {
                "recall": weighted_total,
                "entity": weighted_total,
                "temporal": weighted_total,
                "conceptual": weighted_total,
                "multi_hop": weighted_total,
                "procedural": weighted_total,
            },
        },
    }


@pytest.mark.unit
class TestCompare:
    @pytest.mark.unit
    def test_no_change_passes(self):
        baseline = _make_result(0.90)
        current = _make_result(0.90)
        result = compare(baseline, current)
        assert result["passed"] is True
        assert result["regression"] is False
        assert result["overall_delta"] == pytest.approx(0.0)

    @pytest.mark.unit
    def test_improvement_passes(self):
        result = compare(_make_result(0.80), _make_result(0.85))
        assert result["passed"] is True
        assert result["overall_delta"] == pytest.approx(0.05, abs=0.001)

    @pytest.mark.unit
    def test_regression_within_threshold_passes(self):
        result = compare(_make_result(0.90), _make_result(0.89))
        assert result["passed"] is True
        assert result["regression"] is False

    @pytest.mark.unit
    def test_small_regression_within_threshold_passes(self):
        # Drop well within the threshold should still pass
        result = compare(_make_result(0.90), _make_result(0.885))
        assert result["regression"] is False

    @pytest.mark.unit
    def test_regression_exceeds_threshold_fails(self):
        result = compare(_make_result(0.90), _make_result(0.87))
        assert result["passed"] is False
        assert result["regression"] is True

    @pytest.mark.unit
    def test_category_below_floor_fails(self):
        baseline = _make_result(0.90, {"recall": 0.90, "entity": 0.90})
        current = _make_result(0.90, {"recall": 0.30, "entity": 0.90})
        result = compare(baseline, current)
        assert result["passed"] is False
        assert "recall" in result["category_fails"]

    @pytest.mark.unit
    def test_category_warn_does_not_fail(self):
        baseline = _make_result(0.90, {"recall": 0.90, "entity": 0.90})
        current = _make_result(0.90, {"recall": 0.88, "entity": 0.90})
        result = compare(baseline, current)
        assert result["passed"] is True
        assert "recall" in result["category_warns"]

    @pytest.mark.unit
    def test_summary_lines_include_category_table(self):
        result = compare(_make_result(0.90), _make_result(0.85))
        joined = "\n".join(result["summary_lines"])
        assert "recall" in joined

    @pytest.mark.unit
    def test_summary_lines_include_pass_on_pass(self):
        result = compare(_make_result(0.90), _make_result(0.91))
        assert any("PASS" in line for line in result["summary_lines"])

    @pytest.mark.unit
    def test_summary_lines_include_fail_on_regression(self):
        result = compare(_make_result(0.90), _make_result(0.85))
        assert any("FAIL" in line for line in result["summary_lines"])


@pytest.mark.unit
class TestMockContractSuite:
    """Integration: contract suite runs against mock backend with expected score range."""

    @pytest.mark.unit
    def test_contract_suite_scores_above_floor(self):
        """The contract suite with mock backend should score > 0.85 overall."""
        from kairix.benchmark.runner import run_benchmark
        from kairix.benchmark.suite import load_suite

        suite_path = Path(__file__).parent.parent.parent / "suites" / "contract-suite.yaml"
        if not suite_path.exists():
            pytest.skip("contract-suite.yaml not found")

        suite = load_suite(str(suite_path))
        result = run_benchmark(suite, system="mock", agent="shared", output_dir=None)
        assert result.summary["weighted_total"] >= 0.85, (
            f"Contract suite weighted_total {result.summary['weighted_total']:.4f} < 0.85 "
            f"(category scores: {result.summary['category_scores']})"
        )

    @pytest.mark.unit
    def test_contract_suite_no_category_below_floor(self):
        """All non-classification categories must be above CATEGORY_FLOOR."""
        from kairix.benchmark.runner import run_benchmark
        from kairix.benchmark.suite import load_suite

        suite_path = Path(__file__).parent.parent.parent / "suites" / "contract-suite.yaml"
        if not suite_path.exists():
            pytest.skip("contract-suite.yaml not found")

        suite = load_suite(str(suite_path))
        result = run_benchmark(suite, system="mock", agent="shared", output_dir=None)
        failing = [
            cat
            for cat, score in result.summary["category_scores"].items()
            if cat != "classification" and score < CATEGORY_FLOOR
        ]
        assert not failing, f"Categories below floor {CATEGORY_FLOOR}: {failing}"
