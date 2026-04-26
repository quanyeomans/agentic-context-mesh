"""
Unit tests for kairix.eval.generate.

All external calls (SQLite, hybrid search, LLM API) are mocked.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from kairix.eval.generate import (
    EnrichmentResult,
    GeneratedQuery,
    GenerationResult,
    build_case,
    enrich_suite,
    generate_queries,
    generate_suite,
)
from kairix.eval.judge import JudgeResult

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_JUDGE_RESULT_WITH_GRADE2 = JudgeResult(
    query="What is the deployment process?",
    grades={"docker-deployment-guide": 2, "ci-cd-pipeline": 1, "readme": 0},
    shuffle_order=["docker-deployment-guide", "ci-cd-pipeline", "readme"],
    judge_model="gpt-4o-mini",
    calibration_passed=True,
)

_JUDGE_RESULT_NO_GRADE2 = JudgeResult(
    query="What is the deployment process?",
    grades={"ci-cd-pipeline": 1, "readme": 0},
    shuffle_order=["ci-cd-pipeline", "readme"],
    judge_model="gpt-4o-mini",
    calibration_passed=True,
)

_JUDGE_RESULT_ALL_ZERO = JudgeResult(
    query="What is the deployment process?",
    grades={"readme": 0, "changelog": 0},
    shuffle_order=["readme", "changelog"],
    judge_model="gpt-4o-mini",
    calibration_passed=True,
)


# ---------------------------------------------------------------------------
# generate_queries
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_generate_queries_returns_list_on_valid_response() -> None:
    """generate_queries returns list of GeneratedQuery from a valid API response."""
    mock_response = json.dumps(
        [
            {"query": "How do I deploy a Docker container?", "intent": "procedural"},
            {"query": "What is the Docker deployment process?", "intent": "recall"},
        ]
    )

    with patch("kairix.eval.generate._call_llm", return_value=mock_response):
        results = generate_queries(
            doc_title="docker-guide",
            doc_body="Deploy with docker build, tag, push, run -d.",
            n=2,
            api_key="test-key",
            endpoint="https://test.openai.azure.com",
        )

    assert len(results) == 2
    assert all(isinstance(q, GeneratedQuery) for q in results)
    assert results[0].query == "How do I deploy a Docker container?"
    assert results[0].intent == "procedural"
    assert results[1].intent == "recall"


@pytest.mark.unit
def test_generate_queries_returns_empty_on_parse_failure() -> None:
    """generate_queries returns [] on JSON parse failure after 2 attempts."""
    with patch("kairix.eval.generate._call_llm", return_value="not a json array"):
        results = generate_queries(
            doc_title="test-doc",
            doc_body="some content",
            n=2,
            api_key="test-key",
            endpoint="https://test.openai.azure.com",
        )

    assert results == []


@pytest.mark.unit
def test_generate_queries_returns_empty_on_api_error() -> None:
    """generate_queries returns [] on API error."""
    with patch("kairix.eval.generate._call_llm", side_effect=OSError("connection error")):
        results = generate_queries(
            doc_title="test-doc",
            doc_body="some content",
            n=2,
            api_key="test-key",
            endpoint="https://test.openai.azure.com",
        )

    assert results == []


@pytest.mark.unit
def test_generate_queries_returns_empty_when_no_credentials() -> None:
    """generate_queries returns [] with empty credentials."""
    results = generate_queries(
        doc_title="test-doc",
        doc_body="some content",
        n=2,
        api_key="",
        endpoint="",
    )
    assert results == []


@pytest.mark.unit
def test_generate_queries_defaults_unknown_intent_to_recall() -> None:
    """Unknown intent categories default to 'recall'."""
    mock_response = json.dumps(
        [
            {"query": "What does this doc cover?", "intent": "unknown_category_xyz"},
        ]
    )

    with patch("kairix.eval.generate._call_llm", return_value=mock_response):
        results = generate_queries(
            doc_title="test",
            doc_body="content",
            n=1,
            api_key="test-key",
            endpoint="https://test",
        )

    assert len(results) == 1
    assert results[0].intent == "recall"


# ---------------------------------------------------------------------------
# build_case
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_build_case_returns_case_with_grade2_doc() -> None:
    """build_case returns a valid case dict when grade-2 doc exists."""
    case = build_case(
        query="What is the deployment process?",
        intent="procedural",
        judge_result=_JUDGE_RESULT_WITH_GRADE2,
        paths=["docker-deployment-guide.md", "ci-cd-pipeline.md", "readme.md"],
        snippets=["snippet1", "snippet2", "snippet3"],
        case_id="GEN-P001",
    )

    assert case is not None
    assert case["id"] == "GEN-P001"
    assert case["category"] == "procedural"
    assert case["score_method"] == "ndcg"
    # gold_titles should include grade>=1 docs
    gold_titles = case["gold_titles"]
    assert any(g["title"] == "docker-deployment-guide" and g["relevance"] == 2 for g in gold_titles)
    assert any(g["title"] == "ci-cd-pipeline" and g["relevance"] == 1 for g in gold_titles)
    # grade-0 docs excluded
    assert not any(g["title"] == "readme" for g in gold_titles)


@pytest.mark.unit
def test_build_case_returns_none_when_no_grade2() -> None:
    """build_case returns None when no grade-2 document found."""
    result = build_case(
        query="What is the deployment process?",
        intent="recall",
        judge_result=_JUDGE_RESULT_NO_GRADE2,
        paths=["ci-cd-pipeline.md", "readme.md"],
        snippets=["snippet1", "snippet2"],
        case_id="GEN-R001",
    )

    assert result is None


@pytest.mark.unit
def test_build_case_returns_none_when_all_zero() -> None:
    """build_case returns None when all grades are 0."""
    result = build_case(
        query="irrelevant query",
        intent="recall",
        judge_result=_JUDGE_RESULT_ALL_ZERO,
        paths=["readme.md", "changelog.md"],
        snippets=["a", "b"],
        case_id="GEN-R002",
    )

    assert result is None


@pytest.mark.unit
def test_build_case_gold_titles_sorted_by_relevance_desc() -> None:
    """gold_titles are sorted with highest relevance first."""
    case = build_case(
        query="test query",
        intent="recall",
        judge_result=_JUDGE_RESULT_WITH_GRADE2,
        paths=["docker-deployment-guide.md", "ci-cd-pipeline.md", "readme.md"],
        snippets=["s1", "s2", "s3"],
        case_id="GEN-R001",
    )

    assert case is not None
    gold = case["gold_titles"]
    relevances = [g["relevance"] for g in gold]
    assert relevances == sorted(relevances, reverse=True)


# ---------------------------------------------------------------------------
# generate_suite — smoke test (mocked dependencies)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_generate_suite_writes_valid_yaml(tmp_path: Path) -> None:
    """generate_suite writes a YAML file parseable by yaml.safe_load."""
    output = tmp_path / "test-suite.yaml"

    mock_docs = [
        {"path": "docs/docker-guide.md", "title": "Docker Guide", "collection": "knowledge", "body": "x" * 500},
        {"path": "docs/api-guide.md", "title": "API Guide", "collection": "knowledge", "body": "y" * 500},
    ]
    mock_queries = [
        GeneratedQuery(
            query="How do I deploy?",
            intent="procedural",
            source_doc_path="docs/docker-guide.md",
            source_doc_title="Docker Guide",
        ),
    ]
    mock_judge = _JUDGE_RESULT_WITH_GRADE2

    with (
        patch("kairix.eval.generate.sample_documents", return_value=mock_docs),
        patch("kairix.eval.generate.generate_queries", return_value=mock_queries),
        patch(
            "kairix.eval.generate._retrieve",
            return_value=(["docker-deployment-guide.md", "ci-cd-pipeline.md"], ["s1", "s2"]),
        ),
        patch("kairix.eval.generate.judge_batch", return_value=mock_judge),
        patch("kairix.eval.generate.fetch_llm_credentials", return_value=("key", "https://ep", "gpt-4o-mini")),
    ):
        generate_suite(
            output_path=str(output),
            n_cases=5,
            calibrate_first=False,
        )

    assert output.exists()
    with open(output, encoding="utf-8") as f:
        parsed = yaml.safe_load(f)

    assert "cases" in parsed
    assert isinstance(parsed["cases"], list)


@pytest.mark.unit
def test_generate_suite_returns_result_on_empty_docs(tmp_path: Path) -> None:
    """generate_suite returns a GenerationResult (not raises) when no docs sampled."""
    output = tmp_path / "empty-suite.yaml"

    with (
        patch("kairix.eval.generate.sample_documents", return_value=[]),
        patch("kairix.eval.generate.fetch_llm_credentials", return_value=("key", "https://ep", "gpt-4o-mini")),
    ):
        result = generate_suite(
            output_path=str(output),
            n_cases=10,
            calibrate_first=False,
        )

    assert isinstance(result, GenerationResult)
    assert result.n_accepted == 0
    assert len(result.errors) > 0


# ---------------------------------------------------------------------------
# enrich_suite — smoke test
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_enrich_suite_writes_valid_yaml_with_gold_titles(tmp_path: Path) -> None:
    """enrich_suite enriches cases with gold_titles and writes valid YAML."""
    # Write a minimal input suite
    input_suite = {
        "meta": {"version": "1.0"},
        "cases": [
            {
                "id": "R001",
                "category": "recall",
                "query": "What is the deployment process?",
                "gold_path": "docker-guide.md",
                "score_method": "exact",
            }
        ],
    }
    input_path = tmp_path / "input.yaml"
    output_path = tmp_path / "output.yaml"

    with open(input_path, "w", encoding="utf-8") as f:
        yaml.dump(input_suite, f)

    with (
        patch(
            "kairix.eval.generate._retrieve",
            return_value=(["docker-deployment-guide.md", "ci-cd-pipeline.md"], ["s1", "s2"]),
        ),
        patch("kairix.eval.generate.judge_batch", return_value=_JUDGE_RESULT_WITH_GRADE2),
        patch("kairix.eval.generate.fetch_llm_credentials", return_value=("key", "https://ep", "gpt-4o-mini")),
    ):
        result = enrich_suite(
            suite_path=str(input_path),
            output_path=str(output_path),
        )

    assert isinstance(result, EnrichmentResult)
    assert result.n_cases == 1
    assert output_path.exists()

    with open(output_path, encoding="utf-8") as f:
        parsed = yaml.safe_load(f)

    assert "cases" in parsed
    case = parsed["cases"][0]
    assert "gold_titles" in case
    assert case["score_method"] == "ndcg"


@pytest.mark.unit
def test_enrich_suite_preserves_existing_fields(tmp_path: Path) -> None:
    """enrich_suite preserves all case fields not updated by enrichment."""
    input_suite = {
        "meta": {"version": "1.0"},
        "cases": [
            {
                "id": "T001",
                "category": "temporal",
                "query": "What happened last week?",
                "gold_path": "daily-log.md",
                "score_method": "exact",
                "notes": "Important temporal case",
                "agent": "builder",
            }
        ],
    }
    input_path = tmp_path / "input.yaml"
    output_path = tmp_path / "output.yaml"
    with open(input_path, "w", encoding="utf-8") as f:
        yaml.dump(input_suite, f)

    with (
        patch("kairix.eval.generate._retrieve", return_value=(["daily-log.md"], ["snippet"])),
        patch(
            "kairix.eval.generate.judge_batch",
            return_value=JudgeResult(
                query="q", grades={"daily-log": 2}, shuffle_order=["daily-log"], judge_model="gpt-4o-mini"
            ),
        ),
        patch("kairix.eval.generate.fetch_llm_credentials", return_value=("k", "https://ep", "gpt-4o-mini")),
    ):
        enrich_suite(suite_path=str(input_path), output_path=str(output_path))

    with open(output_path, encoding="utf-8") as f:
        parsed = yaml.safe_load(f)

    case = parsed["cases"][0]
    assert case["id"] == "T001"
    assert case["category"] == "temporal"
    assert case["notes"] == "Important temporal case"
    assert case["agent"] == "builder"


@pytest.mark.unit
def test_enrich_suite_skips_case_when_no_relevant_doc(tmp_path: Path) -> None:
    """enrich_suite keeps original case when no grade>=1 doc found."""
    input_suite = {
        "meta": {},
        "cases": [
            {
                "id": "R001",
                "category": "recall",
                "query": "obscure query",
                "gold_path": "obscure-doc.md",
                "score_method": "exact",
            }
        ],
    }
    input_path = tmp_path / "input.yaml"
    output_path = tmp_path / "output.yaml"
    with open(input_path, "w", encoding="utf-8") as f:
        yaml.dump(input_suite, f)

    with (
        patch("kairix.eval.generate._retrieve", return_value=(["unrelated.md"], ["snippet"])),
        patch("kairix.eval.generate.judge_batch", return_value=_JUDGE_RESULT_ALL_ZERO),
        patch("kairix.eval.generate.fetch_llm_credentials", return_value=("k", "https://ep", "gpt-4o-mini")),
    ):
        result = enrich_suite(suite_path=str(input_path), output_path=str(output_path))

    assert result.n_skipped == 1
    assert result.n_enriched == 0

    with open(output_path, encoding="utf-8") as f:
        parsed = yaml.safe_load(f)
    # Original gold_path preserved
    assert parsed["cases"][0].get("gold_path") == "obscure-doc.md"
