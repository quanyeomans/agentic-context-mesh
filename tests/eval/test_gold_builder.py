"""Unit tests for kairix.eval.gold_builder — TREC pooling and gold suite building."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import yaml

from kairix.eval.gold_builder import (
    GoldBuildReport,
    PooledCandidate,
    grade_candidates,
    pool_candidates,
)

# ---------------------------------------------------------------------------
# pool_candidates
# ---------------------------------------------------------------------------


class TestPoolCandidates:
    @pytest.mark.unit
    @patch("kairix.eval.gold_builder._bm25_search_with_weights")
    def test_pools_from_bm25(self, mock_bm25):
        mock_bm25.return_value = [
            {"path": "/doc1.md", "title": "Doc 1", "snippet": "text", "collection": "eng"},
            {"path": "/doc2.md", "title": "Doc 2", "snippet": "text", "collection": "eng"},
        ]
        result = pool_candidates("test query", ["bm25-equal"])
        assert len(result) == 2
        assert all(isinstance(c, PooledCandidate) for c in result)

    @pytest.mark.unit
    @patch("kairix.eval.gold_builder._bm25_search_with_weights")
    def test_deduplicates_across_systems(self, mock_bm25):
        mock_bm25.return_value = [
            {"path": "/doc1.md", "title": "Doc 1", "snippet": "text", "collection": "eng"},
        ]
        result = pool_candidates("test query", ["bm25-equal", "bm25-qmd"])
        assert len(result) == 1
        assert "bm25-equal" in result[0].sources
        assert "bm25-qmd" in result[0].sources

    @pytest.mark.unit
    @patch("kairix.eval.gold_builder._vector_search")
    @patch("kairix.eval.gold_builder._bm25_search_with_weights")
    def test_pools_bm25_and_vector(self, mock_bm25, mock_vector):
        mock_bm25.return_value = [
            {"path": "/doc1.md", "title": "Doc 1", "snippet": "text", "collection": "eng"},
        ]
        mock_vector.return_value = [
            {"path": "/doc2.md", "title": "Doc 2", "snippet": "text", "collection": "eng"},
        ]
        result = pool_candidates("test query", ["bm25-equal", "vector"])
        assert len(result) == 2

    @pytest.mark.unit
    @patch("kairix.eval.gold_builder._bm25_search_with_weights")
    def test_unknown_system_skipped(self, mock_bm25):
        mock_bm25.return_value = []
        result = pool_candidates("test query", ["bm25-equal", "nosuchsystem"])
        assert isinstance(result, list)

    @pytest.mark.unit
    @patch("kairix.eval.gold_builder._bm25_search_with_weights")
    def test_candidate_fields(self, mock_bm25):
        mock_bm25.return_value = [
            {"path": "/eng/doc.md", "title": "Title", "snippet": "Some text", "collection": "eng"},
        ]
        result = pool_candidates("query", ["bm25-equal"])
        c = result[0]
        assert c.path == "/eng/doc.md"
        assert c.title == "Title"
        assert c.snippet == "Some text"
        assert c.collection == "eng"


# ---------------------------------------------------------------------------
# grade_candidates
# ---------------------------------------------------------------------------


class TestGradeCandidates:
    @pytest.mark.unit
    @patch("kairix.eval.gold_builder.judge_batch")
    def test_grades_assigned(self, mock_judge):
        mock_result = MagicMock()
        mock_result.grades = {"doc1": 2, "doc2": 1}
        mock_judge.return_value = mock_result

        candidates = [
            PooledCandidate(path="/path/doc1.md", title="Doc 1", snippet="text", collection="eng"),
            PooledCandidate(path="/path/doc2.md", title="Doc 2", snippet="text", collection="eng"),
        ]

        result = grade_candidates("query", candidates, "key", "endpoint", judge_runs=2)
        assert result[0].grade == 2
        assert result[1].grade == 1

    @pytest.mark.unit
    @patch("kairix.eval.gold_builder.judge_batch")
    def test_majority_vote(self, mock_judge):
        """Two runs with different grades — majority wins."""
        call_count = [0]

        def side_effect(**kwargs):
            call_count[0] += 1
            mock_result = MagicMock()
            if call_count[0] == 1:
                mock_result.grades = {"doc1": 2}
            else:
                mock_result.grades = {"doc1": 1}
            return mock_result

        mock_judge.side_effect = side_effect

        candidates = [
            PooledCandidate(path="/path/doc1.md", title="Doc 1", snippet="text", collection="eng"),
        ]
        result = grade_candidates("query", candidates, "key", "endpoint", judge_runs=2)
        # With 2 runs and different grades, majority vote picks one
        assert result[0].grade in (1, 2)
        assert len(result[0].grade_votes) == 2

    @pytest.mark.unit
    def test_empty_candidates(self):
        result = grade_candidates("query", [], "key", "endpoint")
        assert result == []

    @pytest.mark.unit
    @patch("kairix.eval.gold_builder.judge_batch")
    def test_three_runs_majority(self, mock_judge):
        """Three runs — grade 2 appears twice, should win."""
        call_count = [0]

        def side_effect(**kwargs):
            call_count[0] += 1
            mock_result = MagicMock()
            if call_count[0] <= 2:
                mock_result.grades = {"doc1": 2}
            else:
                mock_result.grades = {"doc1": 0}
            return mock_result

        mock_judge.side_effect = side_effect

        candidates = [
            PooledCandidate(path="/path/doc1.md", title="Doc 1", snippet="text", collection="eng"),
        ]
        result = grade_candidates("query", candidates, "key", "endpoint", judge_runs=3)
        assert result[0].grade == 2


# ---------------------------------------------------------------------------
# build_independent_gold (integration-level mock test)
# ---------------------------------------------------------------------------


class TestBuildIndependentGold:
    @pytest.mark.unit
    @patch("kairix.eval.gold_builder.fetch_llm_credentials")
    @patch("kairix.eval.gold_builder.calibrate")
    @patch("kairix.eval.gold_builder.pool_candidates")
    @patch("kairix.eval.gold_builder.grade_candidates")
    def test_full_build(self, mock_grade, mock_pool, mock_calibrate, mock_creds, tmp_path):
        from kairix.eval.gold_builder import build_independent_gold

        mock_creds.return_value = ("api-key", "https://endpoint", "gpt-4o-mini")

        mock_pool.return_value = [
            PooledCandidate(
                path="/eng/relevant.md",
                title="Relevant",
                snippet="Good content",
                collection="eng",
                sources=["bm25-equal"],
            ),
            PooledCandidate(
                path="/eng/irrelevant.md",
                title="Irrelevant",
                snippet="Bad content",
                collection="eng",
                sources=["bm25-qmd"],
            ),
        ]

        def fake_grade(query, candidates, *args, **kwargs):
            for c in candidates:
                if "relevant" in c.path:
                    c.grade = 2
                    c.grade_votes = [2, 2]
                else:
                    c.grade = 0
                    c.grade_votes = [0, 0]
            return candidates

        mock_grade.side_effect = fake_grade

        suite_path = tmp_path / "suite.yaml"
        suite_path.write_text(
            yaml.dump(
                {
                    "cases": [
                        {"query": "test query", "category": "recall", "score_method": "ndcg"},
                    ],
                }
            )
        )

        output_path = tmp_path / "output" / "gold.yaml"
        report = build_independent_gold(suite_path, output_path, systems=["bm25-equal"])

        assert report.queries_processed == 1
        assert report.total_candidates_pooled == 2
        assert output_path.exists()

        output = yaml.safe_load(output_path.read_text())
        gold_titles = output["cases"][0]["gold_titles"]
        assert any("relevant" in g["title"] for g in gold_titles)
        assert output["meta"]["gold_method"] == "trec-pooling-llm-judge"

    @pytest.mark.unit
    @patch("kairix.eval.gold_builder.fetch_llm_credentials")
    def test_no_credentials(self, mock_creds, tmp_path):
        from kairix.eval.gold_builder import build_independent_gold

        mock_creds.return_value = ("", "", "")

        suite_path = tmp_path / "suite.yaml"
        suite_path.write_text(yaml.dump({"cases": [{"query": "q"}]}))

        report = build_independent_gold(suite_path, tmp_path / "out.yaml")
        assert report.queries_processed == 0

    @pytest.mark.unit
    def test_gold_build_report_defaults(self):
        report = GoldBuildReport()
        assert report.queries_processed == 0
        assert report.grade_distribution == {0: 0, 1: 0, 2: 0}
