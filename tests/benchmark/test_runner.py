"""
Tests for kairix.benchmark.runner — covers previously-untested paths:
- _exact_match(): gold path matching variants
- _fuzzy_match(): partial path matching
- _classification_score(): rule classifier integration
- _llm_judge(): API call mocked + error paths
- _score_tier(): tier labels
- _category_diagnosis(): diagnostic strings
- format_interpretation(): output structure
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from kairix.benchmark.runner import (
    BenchmarkResult,
    _category_diagnosis,
    _classification_score,
    _exact_match,
    _fuzzy_match,
    _llm_judge,
    _score_tier,
    format_interpretation,
)

# ---------------------------------------------------------------------------
# _exact_match
# ---------------------------------------------------------------------------


def test_exact_match_returns_1_for_direct_match() -> None:
    paths = ["04-Agent-Knowledge/builder/patterns.md", "some/other/doc.md"]
    assert _exact_match(paths, "04-Agent-Knowledge/builder/patterns.md") == 1.0


def test_exact_match_returns_1_for_substring_match() -> None:
    paths = ["04-Agent-Knowledge/builder/patterns.md"]
    assert _exact_match(paths, "builder/patterns") == 1.0


def test_exact_match_returns_0_when_no_match() -> None:
    paths = ["some/unrelated/doc.md"]
    assert _exact_match(paths, "builder/patterns.md") == 0.0


def test_exact_match_returns_0_for_empty_gold() -> None:
    assert _exact_match(["any/path.md"], "") == 0.0


def test_exact_match_returns_0_for_empty_paths() -> None:
    assert _exact_match([], "builder/patterns.md") == 0.0


def test_exact_match_is_case_insensitive() -> None:
    paths = ["04-Agent-Knowledge/Builder/Patterns.md"]
    assert _exact_match(paths, "builder/patterns.md") == 1.0


# ---------------------------------------------------------------------------
# _fuzzy_match
# ---------------------------------------------------------------------------


def test_fuzzy_match_returns_1_for_suffix_match() -> None:
    paths = ["04-Agent-Knowledge/entities/alice-chen.md"]
    assert _fuzzy_match(paths, "entities/alice-chen.md") == 1.0


def test_fuzzy_match_returns_0_for_no_match() -> None:
    paths = ["totally/unrelated/file.md"]
    assert _fuzzy_match(paths, "entities/alice-chen.md") == 0.0


def test_fuzzy_match_returns_0_for_empty_gold() -> None:
    assert _fuzzy_match(["any/path.md"], "") == 0.0


def test_fuzzy_match_respects_topk_limit() -> None:
    # gold is in position 11 (0-indexed), beyond top-10
    paths = [f"unrelated/{i}.md" for i in range(10)] + ["04-Agent-Knowledge/entities/target.md"]
    assert _fuzzy_match(paths, "entities/target.md") == 0.0


# ---------------------------------------------------------------------------
# _classification_score
# ---------------------------------------------------------------------------


def test_classification_score_returns_1_for_correct_type() -> None:
    """Returns 1.0 when classifier returns the expected type."""
    mock_result = MagicMock()
    mock_result.type = "decision"

    with (
        patch("kairix.classify.rules.classify_content", return_value=mock_result),
        patch("kairix.benchmark.runner.classify_content", return_value=mock_result, create=True),
    ):
        score = _classification_score("We decided to use PostgreSQL.", "decision")

    assert score == 1.0


def test_classification_score_returns_0_for_wrong_type() -> None:
    """Returns 0.0 when classifier returns a different type."""
    mock_result = MagicMock()
    mock_result.type = "pattern"

    with (
        patch("kairix.classify.rules.classify_content", return_value=mock_result),
        patch("kairix.benchmark.runner.classify_content", return_value=mock_result, create=True),
    ):
        score = _classification_score("We decided to use PostgreSQL.", "decision")

    assert score == 0.0


def test_classification_score_returns_0_on_exception() -> None:
    """Returns 0.0 when classifier raises an exception."""
    with patch("kairix.classify.rules.classify_content", side_effect=RuntimeError("oops")):
        score = _classification_score("anything", "decision")

    assert score == 0.0


def test_classification_score_tries_llm_when_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    """Falls back to LLM judge when rules return 'unknown'."""
    unknown_result = MagicMock()
    unknown_result.type = "unknown"

    llm_result = MagicMock()
    llm_result.type = "decision"

    with (
        patch("kairix.classify.rules.classify_content", return_value=unknown_result),
        patch("kairix.classify.judge.classify_with_llm", return_value=llm_result),
    ):
        score = _classification_score("We decided to use PostgreSQL.", "decision")

    assert score == 1.0


# ---------------------------------------------------------------------------
# _llm_judge
# ---------------------------------------------------------------------------


def test_llm_judge_returns_score_from_api() -> None:
    """Returns float score from API response."""
    mock_resp_body = b'{"choices":[{"message":{"content":"0.8"}}]}'

    mock_response = MagicMock()
    mock_response.read.return_value = mock_resp_body
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_response):
        score = _llm_judge(
            query="what are our engineering patterns?",
            paths=["04-Agent-Knowledge/builder/patterns.md"],
            snippets=["Engineering patterns for Builder"],
            api_key="fake-key",
            endpoint="https://fake.example.com",
            deployment="gpt-4o-mini",
        )

    assert score == pytest.approx(0.8)


def test_llm_judge_clamps_to_range() -> None:
    """Clamps score to [0.0, 1.0]."""
    mock_resp_body = b'{"choices":[{"message":{"content":"1.5"}}]}'
    mock_response = MagicMock()
    mock_response.read.return_value = mock_resp_body
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_response):
        score = _llm_judge("q", ["p.md"], ["s"], "k", "https://e.com")

    assert score == 1.0


def test_llm_judge_returns_0_on_api_error() -> None:
    """Returns 0.0 when API call fails."""
    with patch("urllib.request.urlopen", side_effect=OSError("timeout")):
        score = _llm_judge("q", ["p.md"], ["s"], "k", "https://e.com")

    assert score == 0.0


def test_llm_judge_returns_0_for_empty_paths() -> None:
    """Returns 0.0 immediately when no paths are provided."""
    score = _llm_judge("q", [], [], "k", "https://e.com")
    assert score == 0.0


def test_llm_judge_returns_0_on_bad_json() -> None:
    """Returns 0.0 when response body is not valid JSON."""
    mock_response = MagicMock()
    mock_response.read.return_value = b"not json"
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_response):
        score = _llm_judge("q", ["p.md"], ["s"], "k", "https://e.com")

    assert score == 0.0


# ---------------------------------------------------------------------------
# _score_tier
# ---------------------------------------------------------------------------


def test_score_tier_production() -> None:
    assert "Production" in _score_tier(0.762)


def test_score_tier_stable() -> None:
    assert "Phase 2" in _score_tier(0.69)


def test_score_tier_developing() -> None:
    assert "BM25" in _score_tier(0.61)


def test_score_tier_needs_work() -> None:
    assert "BM25" in _score_tier(0.45)


# ---------------------------------------------------------------------------
# _category_diagnosis
# ---------------------------------------------------------------------------


def test_category_diagnosis_temporal_low() -> None:
    msg = _category_diagnosis("temporal", 0.3)
    assert "temporal" in msg.lower() or "date" in msg.lower() or "chunking" in msg.lower()


def test_category_diagnosis_entity_low() -> None:
    msg = _category_diagnosis("entity", 0.3)
    assert len(msg) > 0  # Just verify it returns a non-empty string


def test_category_diagnosis_unknown_category() -> None:
    msg = _category_diagnosis("nonexistent_category", 0.5)
    assert isinstance(msg, str)  # Should not crash


# ---------------------------------------------------------------------------
# format_interpretation
# ---------------------------------------------------------------------------


def test_format_interpretation_returns_string() -> None:
    result = BenchmarkResult(
        meta={"suite_name": "test-suite", "system": "hybrid", "date": "2026-03-23", "n_cases": 4},
        summary={
            "weighted_total": 0.762,
            "category_scores": {
                "recall": 0.875,
                "entity": 0.933,
                "classification": 1.0,
            },
            "gates": {"phase1": True, "phase2": True, "phase3": True},
        },
        diagnostics={},
        cases=[],
    )
    output = format_interpretation(result)
    assert "0.762" in output
    assert isinstance(output, str)
    assert len(output) > 50
