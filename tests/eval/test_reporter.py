"""Unit tests for kairix.quality.eval.reporter — performance report generation."""

from __future__ import annotations

import json

import pytest

from kairix.quality.eval.reporter import PerformanceReporter


@pytest.fixture()
def results_file(tmp_path):
    """Create a benchmark results JSON file."""
    data = {
        "overall_ndcg": 0.85,
        "checks": [
            {"category": "recall", "ndcg": 0.90},
            {"category": "temporal", "ndcg": 0.60},
            {"category": "entity", "ndcg": 0.82},
            {"category": "contextual_prep", "ndcg": 0.65},
            {"category": "conceptual", "ndcg": 0.75},
        ],
    }
    path = tmp_path / "results.json"
    path.write_text(json.dumps(data))
    return path


@pytest.fixture()
def failing_results_file(tmp_path):
    """Results that fail the temporal gate."""
    data = {
        "overall_ndcg": 0.70,
        "checks": [
            {"category": "recall", "ndcg": 0.80},
            {"category": "temporal", "ndcg": 0.40},
            {"category": "entity", "ndcg": 0.85},
            {"category": "contextual_prep", "ndcg": 0.65},
        ],
    }
    path = tmp_path / "failing.json"
    path.write_text(json.dumps(data))
    return path


class TestPerformanceReporter:
    @pytest.mark.unit
    def test_load_results(self, results_file):
        reporter = PerformanceReporter(results_file)
        assert reporter._results["overall_ndcg"] == pytest.approx(0.85)

    @pytest.mark.unit
    def test_load_missing_file(self, tmp_path):
        reporter = PerformanceReporter(tmp_path / "nonexistent.json")
        assert reporter._results == {}

    @pytest.mark.unit
    def test_passes_gates_true(self, results_file):
        reporter = PerformanceReporter(results_file)
        assert reporter.passes_gates() is True

    @pytest.mark.unit
    def test_passes_gates_false(self, failing_results_file):
        reporter = PerformanceReporter(failing_results_file)
        assert reporter.passes_gates() is False

    @pytest.mark.unit
    def test_gate_failures_empty_when_passing(self, results_file):
        reporter = PerformanceReporter(results_file)
        assert reporter.gate_failures() == []

    @pytest.mark.unit
    def test_gate_failures_identifies_failing_category(self, failing_results_file):
        reporter = PerformanceReporter(failing_results_file)
        failures = reporter.gate_failures()
        cats = [f[0] for f in failures]
        assert "temporal" in cats
        assert "overall" in cats

    @pytest.mark.unit
    def test_gate_failure_tuple_structure(self, failing_results_file):
        reporter = PerformanceReporter(failing_results_file)
        failures = reporter.gate_failures()
        temporal = next(f for f in failures if f[0] == "temporal")
        assert temporal[1] == pytest.approx(0.40)  # actual
        assert temporal[2] == pytest.approx(0.55)  # threshold

    @pytest.mark.unit
    def test_markdown_report_header(self, results_file):
        reporter = PerformanceReporter(results_file)
        md = reporter.markdown_report()
        assert "# Kairix Benchmark" in md

    @pytest.mark.unit
    def test_markdown_report_table(self, results_file):
        reporter = PerformanceReporter(results_file)
        md = reporter.markdown_report()
        assert "| Category | Score | Gate | Status | Delta |" in md

    @pytest.mark.unit
    def test_markdown_report_pass_status(self, results_file):
        reporter = PerformanceReporter(results_file)
        md = reporter.markdown_report()
        assert "pass" in md

    @pytest.mark.unit
    def test_markdown_report_fail_status(self, failing_results_file):
        reporter = PerformanceReporter(failing_results_file)
        md = reporter.markdown_report()
        assert "FAIL" in md

    @pytest.mark.unit
    def test_markdown_report_gate_summary_pass(self, results_file):
        reporter = PerformanceReporter(results_file)
        md = reporter.markdown_report()
        assert "All gates passed" in md

    @pytest.mark.unit
    def test_markdown_report_gate_summary_fail(self, failing_results_file):
        reporter = PerformanceReporter(failing_results_file)
        md = reporter.markdown_report()
        assert "Gate failures" in md

    @pytest.mark.unit
    def test_markdown_report_with_previous(self, results_file, tmp_path):
        prev = {
            "overall_ndcg": 0.80,
            "checks": [
                {"category": "recall", "ndcg": 0.85},
                {"category": "temporal", "ndcg": 0.55},
            ],
        }
        prev_path = tmp_path / "previous.json"
        prev_path.write_text(json.dumps(prev))
        reporter = PerformanceReporter(results_file)
        md = reporter.markdown_report(previous_path=prev_path)
        # Should contain delta values (positive)
        assert "+" in md

    @pytest.mark.unit
    def test_custom_gates(self, results_file):
        reporter = PerformanceReporter(results_file, gates={"overall": 0.99})
        assert reporter.passes_gates() is False

    @pytest.mark.unit
    def test_category_scores_extraction(self, results_file):
        reporter = PerformanceReporter(results_file)
        scores = reporter._category_scores(reporter._results)
        assert scores["overall"] == pytest.approx(0.85)
        assert scores["recall"] == pytest.approx(0.90)

    @pytest.mark.unit
    def test_category_scores_ndcg_key(self, tmp_path):
        """Results with 'ndcg' instead of 'overall_ndcg'."""
        data = {"ndcg": 0.77, "checks": []}
        path = tmp_path / "alt.json"
        path.write_text(json.dumps(data))
        reporter = PerformanceReporter(path)
        scores = reporter._category_scores(reporter._results)
        assert scores["overall"] == pytest.approx(0.77)
