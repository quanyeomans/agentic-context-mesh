"""Tests for benchmark baseline comparison module."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kairix.benchmark.baseline import (
    CATEGORY_FLOOR,
    compare,
    load_result,
    run_gate,
)


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
class TestLoadResult:
    @pytest.mark.unit
    def test_load_result_valid_file(self, tmp_path):
        data = _make_result(0.85)
        p = tmp_path / "result.json"
        p.write_text(json.dumps(data))
        loaded = load_result(p)
        assert loaded["summary"]["weighted_total"] == 0.85

    @pytest.mark.unit
    def test_load_result_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_result(tmp_path / "nonexistent.json")

    @pytest.mark.unit
    def test_load_result_missing_summary_key(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text(json.dumps({"meta": {}}))
        with pytest.raises(ValueError, match="missing 'summary'"):
            load_result(p)


@pytest.mark.unit
class TestCompareSkippedCategories:
    @pytest.mark.unit
    def test_both_zero_category_not_flagged(self):
        """When both baseline and current are 0.0, category should not be flagged as below floor."""
        baseline = _make_result(0.90, {"recall": 0.90, "unused_cat": 0.0})
        current = _make_result(0.90, {"recall": 0.90, "unused_cat": 0.0})
        result = compare(baseline, current)
        assert result["passed"] is True
        assert "unused_cat" not in result["category_fails"]
        assert result["category_deltas"]["unused_cat"] == pytest.approx(0.0)

    @pytest.mark.unit
    def test_new_category_in_current_only(self):
        """A category only in current (not in baseline) gets delta calculated."""
        baseline = _make_result(0.90, {"recall": 0.90})
        current = _make_result(0.90, {"recall": 0.90, "new_cat": 0.80})
        result = compare(baseline, current)
        assert "new_cat" in result["category_deltas"]
        assert result["category_deltas"]["new_cat"] == pytest.approx(0.80)

    @pytest.mark.unit
    def test_category_in_baseline_only(self):
        """A category only in baseline (missing from current) gets negative delta."""
        baseline = _make_result(0.90, {"recall": 0.90, "old_cat": 0.80})
        current = _make_result(0.90, {"recall": 0.90})
        result = compare(baseline, current)
        assert "old_cat" in result["category_deltas"]
        assert result["category_deltas"]["old_cat"] == pytest.approx(-0.80)


@pytest.mark.unit
class TestRunGate:
    @pytest.mark.unit
    def test_run_gate_pass(self, tmp_path):
        baseline = _make_result(0.90)
        current = _make_result(0.91)
        bp = tmp_path / "baseline.json"
        cp = tmp_path / "current.json"
        bp.write_text(json.dumps(baseline))
        cp.write_text(json.dumps(current))
        assert run_gate(str(bp), str(cp)) == 0

    @pytest.mark.unit
    def test_run_gate_fail_on_regression(self, tmp_path):
        baseline = _make_result(0.90)
        current = _make_result(0.85)
        bp = tmp_path / "baseline.json"
        cp = tmp_path / "current.json"
        bp.write_text(json.dumps(baseline))
        cp.write_text(json.dumps(current))
        assert run_gate(str(bp), str(cp)) == 1

    @pytest.mark.unit
    def test_run_gate_baseline_not_found(self, tmp_path):
        cp = tmp_path / "current.json"
        cp.write_text(json.dumps(_make_result(0.90)))
        assert run_gate(str(tmp_path / "missing.json"), str(cp)) == 1

    @pytest.mark.unit
    def test_run_gate_current_not_found(self, tmp_path):
        bp = tmp_path / "baseline.json"
        bp.write_text(json.dumps(_make_result(0.90)))
        assert run_gate(str(bp), str(tmp_path / "missing.json")) == 1


@pytest.mark.unit
class TestSummaryLines:
    @pytest.mark.unit
    def test_summary_lines_contain_scores(self):
        result = compare(_make_result(0.90), _make_result(0.91))
        joined = "\n".join(result["summary_lines"])
        assert "0.9000" in joined
        assert "0.9100" in joined

    @pytest.mark.unit
    def test_summary_lines_category_fail_message(self):
        baseline = _make_result(0.90, {"recall": 0.90, "entity": 0.90})
        current = _make_result(0.90, {"recall": 0.30, "entity": 0.90})
        result = compare(baseline, current)
        joined = "\n".join(result["summary_lines"])
        assert "BELOW FLOOR" in joined

    @pytest.mark.unit
    def test_summary_lines_warn_message(self):
        baseline = _make_result(0.90, {"recall": 0.90, "entity": 0.90})
        current = _make_result(0.90, {"recall": 0.88, "entity": 0.90})
        result = compare(baseline, current)
        joined = "\n".join(result["summary_lines"])
        assert "WARN" in joined


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
