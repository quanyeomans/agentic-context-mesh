"""Unit tests for kairix.quality.eval.gold_builder — TREC pooling and gold suite building."""

from __future__ import annotations

import pytest
import yaml

from kairix.quality.eval.gold_builder import (
    GoldBuildReport,
    PooledCandidate,
    grade_candidates,
    path_title,
    pool_candidates,
)
from kairix.quality.eval.judge import JudgeResult

# ---------------------------------------------------------------------------
# pool_candidates
# ---------------------------------------------------------------------------


def _make_bm25_fn(results: list[dict]):
    """Return a search_fn callable that returns fixed results."""

    def _fn(query, collections, limit):
        return results

    return _fn


class TestPoolCandidates:
    @pytest.mark.unit
    def test_pools_from_bm25(self):
        results = [
            {
                "path": "/doc1.md",
                "title": "Doc 1",
                "snippet": "text",
                "collection": "eng",
            },
            {
                "path": "/doc2.md",
                "title": "Doc 2",
                "snippet": "text",
                "collection": "eng",
            },
        ]
        result = pool_candidates(
            "test query",
            ["bm25-equal"],
            search_fns={"bm25-equal": _make_bm25_fn(results)},
        )
        assert len(result) == 2
        assert all(isinstance(c, PooledCandidate) for c in result)

    @pytest.mark.unit
    def test_deduplicates_across_systems(self):
        results = [
            {
                "path": "/doc1.md",
                "title": "Doc 1",
                "snippet": "text",
                "collection": "eng",
            },
        ]
        fn = _make_bm25_fn(results)
        result = pool_candidates(
            "test query",
            ["bm25-equal", "bm25-filepath"],
            search_fns={"bm25-equal": fn, "bm25-filepath": fn},
        )
        assert len(result) == 1
        assert "bm25-equal" in result[0].sources
        assert "bm25-filepath" in result[0].sources

    @pytest.mark.unit
    def test_pools_bm25_and_vector(self):
        bm25_results = [
            {
                "path": "/doc1.md",
                "title": "Doc 1",
                "snippet": "text",
                "collection": "eng",
            },
        ]
        vector_results = [
            {
                "path": "/doc2.md",
                "title": "Doc 2",
                "snippet": "text",
                "collection": "eng",
            },
        ]
        result = pool_candidates(
            "test query",
            ["bm25-equal", "vector"],
            search_fns={
                "bm25-equal": _make_bm25_fn(bm25_results),
                "vector": _make_bm25_fn(vector_results),
            },
        )
        assert len(result) == 2

    @pytest.mark.unit
    def test_unknown_system_skipped(self):
        result = pool_candidates(
            "test query",
            ["bm25-equal", "nosuchsystem"],
            search_fns={"bm25-equal": _make_bm25_fn([])},
        )
        assert isinstance(result, list)

    @pytest.mark.unit
    def test_candidate_fields(self):
        results = [
            {
                "path": "/eng/doc.md",
                "title": "Title",
                "snippet": "Some text",
                "collection": "eng",
            },
        ]
        result = pool_candidates(
            "query",
            ["bm25-equal"],
            search_fns={"bm25-equal": _make_bm25_fn(results)},
        )
        c = result[0]
        assert c.path == "/eng/doc.md"
        assert c.title == "Title"
        assert c.snippet == "Some text"
        assert c.collection == "eng"


# ---------------------------------------------------------------------------
# grade_candidates
# ---------------------------------------------------------------------------


def _make_judge_fn(grades: dict[str, int]) -> object:
    """Return a judge_fn that returns a JudgeResult with fixed grades."""

    def _fn(**kwargs):
        return JudgeResult(
            query=kwargs.get("query", ""),
            grades=grades,
            shuffle_order=list(grades.keys()),
            judge_model="gpt-4o-mini",
        )

    return _fn


class TestGradeCandidates:
    @pytest.mark.unit
    def test_grades_assigned(self):
        # Keys are path_title() output: "/path/doc1.md" -> "path/doc1"
        grades = {"path/doc1": 2, "path/doc2": 1}

        candidates = [
            PooledCandidate(path="/path/doc1.md", title="Doc 1", snippet="text", collection="eng"),
            PooledCandidate(path="/path/doc2.md", title="Doc 2", snippet="text", collection="eng"),
        ]

        result = grade_candidates(
            "query",
            candidates,
            "key",
            "endpoint",
            judge_runs=2,
            judge_fn=_make_judge_fn(grades),
        )
        assert result[0].grade == 2
        assert result[1].grade == 1

    @pytest.mark.unit
    def test_majority_vote(self):
        """Two runs with different grades — majority wins."""
        call_count = [0]

        def _judge_fn(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                grades = {"path/doc1": 2}
            else:
                grades = {"path/doc1": 1}
            return JudgeResult(
                query=kwargs.get("query", ""),
                grades=grades,
                shuffle_order=list(grades.keys()),
                judge_model="gpt-4o-mini",
            )

        candidates = [
            PooledCandidate(path="/path/doc1.md", title="Doc 1", snippet="text", collection="eng"),
        ]
        result = grade_candidates("query", candidates, "key", "endpoint", judge_runs=2, judge_fn=_judge_fn)
        # With 2 runs and different grades, majority vote picks one
        assert result[0].grade in (1, 2)
        assert len(result[0].grade_votes) == 2

    @pytest.mark.unit
    def test_empty_candidates(self):
        result = grade_candidates("query", [], "key", "endpoint")
        assert result == []

    @pytest.mark.unit
    def test_three_runs_majority(self):
        """Three runs — grade 2 appears twice, should win."""
        call_count = [0]

        def _judge_fn(**kwargs):
            call_count[0] += 1
            if call_count[0] <= 2:
                grades = {"path/doc1": 2}
            else:
                grades = {"path/doc1": 0}
            return JudgeResult(
                query=kwargs.get("query", ""),
                grades=grades,
                shuffle_order=list(grades.keys()),
                judge_model="gpt-4o-mini",
            )

        candidates = [
            PooledCandidate(path="/path/doc1.md", title="Doc 1", snippet="text", collection="eng"),
        ]
        result = grade_candidates("query", candidates, "key", "endpoint", judge_runs=3, judge_fn=_judge_fn)
        assert result[0].grade == 2


# ---------------------------------------------------------------------------
# build_independent_gold (integration-level test with fakes)
# ---------------------------------------------------------------------------


class TestBuildIndependentGold:
    @pytest.mark.unit
    def test_full_build(self, tmp_path):
        from kairix.quality.eval.gold_builder import build_independent_gold

        def fake_bm25(query, collections, limit):
            return [
                {
                    "path": "/eng/relevant.md",
                    "title": "Relevant",
                    "snippet": "Good content",
                    "collection": "eng",
                },
                {
                    "path": "/eng/irrelevant.md",
                    "title": "Irrelevant",
                    "snippet": "Bad content",
                    "collection": "eng",
                },
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

        suite_path = tmp_path / "suite.yaml"
        suite_path.write_text(
            yaml.dump(
                {
                    "cases": [
                        {
                            "query": "test query",
                            "category": "recall",
                            "score_method": "ndcg",
                        },
                    ],
                }
            )
        )

        output_path = tmp_path / "output" / "gold.yaml"
        report = build_independent_gold(
            suite_path,
            output_path,
            systems=["bm25-equal"],
            credentials=("api-key", "https://endpoint", "gpt-4o-mini"),
            search_fns={"bm25-equal": fake_bm25},
            calibrate_fn=lambda *_a: True,
            grade_fn=fake_grade,
        )

        assert report.queries_processed == 1
        assert report.total_candidates_pooled == 2
        assert output_path.exists()

        output = yaml.safe_load(output_path.read_text())
        gold_titles = output["cases"][0]["gold_titles"]
        assert any("relevant" in g["title"] for g in gold_titles)
        assert output["meta"]["gold_method"] == "trec-pooling-llm-judge"

    @pytest.mark.unit
    def test_no_credentials(self, tmp_path):
        from kairix.quality.eval.gold_builder import build_independent_gold

        suite_path = tmp_path / "suite.yaml"
        suite_path.write_text(yaml.dump({"cases": [{"query": "q"}]}))

        report = build_independent_gold(suite_path, tmp_path / "out.yaml", credentials=("", "", ""))
        assert report.queries_processed == 0

    @pytest.mark.unit
    def test_gold_build_report_defaults(self):
        report = GoldBuildReport()
        assert report.queries_processed == 0
        assert report.grade_distribution == {0: 0, 1: 0, 2: 0}


# ---------------------------------------------------------------------------
# path_title uniqueness (Bug 1)
# ---------------------------------------------------------------------------


class TestPathTitle:
    @pytest.mark.unit
    def testpath_title_unique_for_readme_files(self):
        """Two readme.md files in different directories produce different titles."""
        t1 = path_title("reference-library/engineering/adr-examples/readme.md")
        t2 = path_title("reference-library/data-and-analysis/dbt-docs/readme.md")
        assert t1 != t2

    @pytest.mark.unit
    def testpath_title_deep_path(self):
        """Deep paths preserve enough context to be unique."""
        t = path_title("reference-library/agentic-ai/panaversity-agentic/03_ai_protocols/01_mcp/readme.md")
        assert "01_mcp" in t
        assert "readme" in t

    @pytest.mark.unit
    def testpath_title_short_path(self):
        """A short path (2 segments) returns all segments minus extension."""
        t = path_title("collection/doc.md")
        assert t == "collection/doc"

    @pytest.mark.unit
    def testpath_title_single_segment(self):
        """A single-segment path returns just the stem."""
        t = path_title("readme.md")
        assert t == "readme"

    @pytest.mark.unit
    def testpath_title_strips_md_extension(self):
        t = path_title("reference-library/engineering/patterns.md")
        assert not t.endswith(".md")
        assert "patterns" in t


# ---------------------------------------------------------------------------
# grade_candidates — duplicate stem handling (Bug 2)
# ---------------------------------------------------------------------------


class TestGradeCandidatesDuplicateStem:
    @pytest.mark.unit
    def test_grade_candidates_distinguishes_same_stem(self):
        """Two candidates with the same filename stem get independent grades."""
        grades = {"a/readme": 2, "b/readme": 0}

        candidates = [
            PooledCandidate(
                path="col/a/readme.md",
                title="A Readme",
                snippet="good",
                collection="col",
            ),
            PooledCandidate(
                path="col/b/readme.md",
                title="B Readme",
                snippet="bad",
                collection="col",
            ),
        ]

        result = grade_candidates(
            "query",
            candidates,
            "key",
            "endpoint",
            judge_runs=1,
            judge_fn=_make_judge_fn(grades),
        )
        assert result[0].grade == 2
        assert result[1].grade == 0

    @pytest.mark.unit
    def test_judge_receivespath_title_keys(self):
        """judge_batch receives path_title() keys, not bare stems."""
        captured_candidates: list = []

        def _capture_judge(**kwargs):
            captured_candidates.extend(kwargs.get("candidates", []))
            return JudgeResult(
                query=kwargs.get("query", ""),
                grades={},
                shuffle_order=[],
                judge_model="gpt-4o-mini",
            )

        candidates = [
            PooledCandidate(
                path="col/sub/readme.md",
                title="Readme",
                snippet="text",
                collection="col",
            ),
        ]
        grade_candidates(
            "query",
            candidates,
            "key",
            "endpoint",
            judge_runs=1,
            judge_fn=_capture_judge,
        )

        # The key should be "sub/readme", not just "readme"
        assert captured_candidates[0][0] == "sub/readme"
