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
    _dcg,
    _exact_match,
    _fuzzy_match,
    _hit_at_k,
    _hit_at_k_by_title,
    _ideal_dcg,
    _llm_judge,
    _ndcg_score,
    _ndcg_score_by_title,
    _normalise_title,
    _reciprocal_rank,
    _reciprocal_rank_by_title,
    _score_tier,
    _stem_from_path,
    _title_in_retrieved,
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
    paths = ["04-Agent-Knowledge/entities/jordan-blake.md"]
    assert _fuzzy_match(paths, "entities/jordan-blake.md") == 1.0


def test_fuzzy_match_returns_0_for_no_match() -> None:
    paths = ["totally/unrelated/file.md"]
    assert _fuzzy_match(paths, "entities/jordan-blake.md") == 0.0


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


# ---------------------------------------------------------------------------
# NDCG@10 helpers
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_dcg_perfect_relevance() -> None:
    import math

    expected = 2 / math.log2(2) + 1 / math.log2(3)
    assert _dcg([2, 1, 0], k=3) == pytest.approx(expected)


@pytest.mark.unit
def test_dcg_empty_relevances() -> None:
    assert _dcg([], k=10) == 0.0


@pytest.mark.unit
def test_dcg_k_truncates() -> None:
    import math

    assert _dcg([2, 1, 1], k=1) == pytest.approx(2 / math.log2(2))


@pytest.mark.unit
def test_ideal_dcg_sorts_by_relevance() -> None:
    gold = [{"path": "a.md", "relevance": 1}, {"path": "b.md", "relevance": 2}]
    import math

    expected = 2 / math.log2(2) + 1 / math.log2(3)
    assert _ideal_dcg(gold, k=10) == pytest.approx(expected)


@pytest.mark.unit
def test_ndcg_score_perfect_retrieval() -> None:
    gold = [{"path": "a.md", "relevance": 2}, {"path": "b.md", "relevance": 1}]
    retrieved = ["a.md", "b.md", "c.md"]
    assert _ndcg_score(retrieved, gold, k=10) == pytest.approx(1.0)


@pytest.mark.unit
def test_ndcg_score_no_relevant_retrieved() -> None:
    gold = [{"path": "a.md", "relevance": 2}]
    retrieved = ["x.md", "y.md"]
    assert _ndcg_score(retrieved, gold, k=10) == pytest.approx(0.0)


@pytest.mark.unit
def test_ndcg_score_empty_gold() -> None:
    assert _ndcg_score(["a.md", "b.md"], [], k=10) == 0.0


@pytest.mark.unit
def test_ndcg_score_partial_retrieval() -> None:
    gold = [{"path": "a.md", "relevance": 2}, {"path": "b.md", "relevance": 1}]
    retrieved = ["b.md"]
    score = _ndcg_score(retrieved, gold, k=10)
    assert 0.0 < score < 1.0


@pytest.mark.unit
def test_ndcg_score_case_insensitive() -> None:
    gold = [{"path": "Docs/Alpha.md", "relevance": 2}]
    retrieved = ["docs/alpha.md"]
    assert _ndcg_score(retrieved, gold, k=10) == pytest.approx(1.0)


@pytest.mark.unit
def test_ndcg_score_known_value() -> None:
    import math

    gold = [
        {"path": "a.md", "relevance": 2},
        {"path": "b.md", "relevance": 1},
        {"path": "c.md", "relevance": 0},
    ]
    retrieved = ["b.md", "a.md"]
    idcg = _ideal_dcg(gold, k=10)
    actual_dcg = 1 / math.log2(2) + 2 / math.log2(3)
    expected = actual_dcg / idcg
    assert _ndcg_score(retrieved, gold, k=10) == pytest.approx(expected, abs=1e-9)


@pytest.mark.unit
def test_hit_at_k_true_when_relevant_in_top_k() -> None:
    gold = [{"path": "a.md", "relevance": 2}]
    assert _hit_at_k(["x.md", "a.md", "y.md"], gold, k=5) is True


@pytest.mark.unit
def test_hit_at_k_false_when_outside_k() -> None:
    gold = [{"path": "a.md", "relevance": 2}]
    retrieved = ["x.md"] * 5 + ["a.md"]
    assert _hit_at_k(retrieved, gold, k=5) is False


@pytest.mark.unit
def test_hit_at_k_excludes_zero_relevance() -> None:
    gold = [{"path": "a.md", "relevance": 0}]
    assert _hit_at_k(["a.md"], gold, k=5) is False


@pytest.mark.unit
def test_reciprocal_rank_first_position() -> None:
    gold = [{"path": "a.md", "relevance": 1}]
    assert _reciprocal_rank(["a.md", "b.md"], gold, k=10) == pytest.approx(1.0)


@pytest.mark.unit
def test_reciprocal_rank_second_position() -> None:
    gold = [{"path": "b.md", "relevance": 1}]
    assert _reciprocal_rank(["a.md", "b.md", "c.md"], gold, k=10) == pytest.approx(0.5)


@pytest.mark.unit
def test_reciprocal_rank_not_found() -> None:
    gold = [{"path": "x.md", "relevance": 1}]
    assert _reciprocal_rank(["a.md", "b.md"], gold, k=10) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Title-based helpers — _normalise_title, _stem_from_path
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_normalise_title_spaces_to_hyphens() -> None:
    assert _normalise_title("Jordan Blake") == "jordan-blake"


@pytest.mark.unit
def test_normalise_title_underscores_to_hyphens() -> None:
    assert _normalise_title("some_slug") == "some-slug"


@pytest.mark.unit
def test_normalise_title_idempotent() -> None:
    assert _normalise_title("already-normalised") == "already-normalised"


@pytest.mark.unit
def test_normalise_title_mixed_separators() -> None:
    assert _normalise_title("foo  bar--baz") == "foo-bar-baz"


@pytest.mark.unit
def test_normalise_title_strips_leading_trailing_hyphens() -> None:
    assert _normalise_title("-leading-trailing-") == "leading-trailing"


@pytest.mark.unit
def test_stem_from_path_simple_filename() -> None:
    assert _stem_from_path("patterns.md") == "patterns"


@pytest.mark.unit
def test_stem_from_path_deep_vault_path() -> None:
    assert _stem_from_path("02-Areas/00-Clients/Acme-Corp/Acme-Corp.md") == "acme-corp"


@pytest.mark.unit
def test_stem_from_path_entity_path() -> None:
    assert _stem_from_path("entities/person/jordan-blake.md") == "jordan-blake"


@pytest.mark.unit
def test_stem_from_path_dated_log() -> None:
    assert _stem_from_path("agent-memory/builder/2026-04-10.md") == "2026-04-10"


# ---------------------------------------------------------------------------
# _title_in_retrieved
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_title_in_retrieved_exact_match() -> None:
    paths = ["02-Areas/00-Clients/Acme-Corp/Acme-Corp.md", "other/doc.md"]
    assert _title_in_retrieved("Acme Corp", paths, top_k=5) is True


@pytest.mark.unit
def test_title_in_retrieved_entity_slug_match() -> None:
    paths = ["entities/person/jordan-blake.md"]
    assert _title_in_retrieved("jordan-blake", paths, top_k=5) is True


@pytest.mark.unit
def test_title_in_retrieved_no_match() -> None:
    paths = ["some/unrelated/doc.md"]
    assert _title_in_retrieved("jordan-blake", paths, top_k=5) is False


@pytest.mark.unit
def test_title_in_retrieved_respects_top_k() -> None:
    # Gold title is at position 3, but top_k=2 — must not match
    paths = ["a.md", "b.md", "entities/person/jordan-blake.md"]
    assert _title_in_retrieved("jordan-blake", paths, top_k=2) is False


@pytest.mark.unit
def test_title_in_retrieved_case_insensitive() -> None:
    paths = ["Vault/JORDAN-BLAKE.md"]
    assert _title_in_retrieved("Jordan Blake", paths, top_k=5) is True


# ---------------------------------------------------------------------------
# _ndcg_score_by_title
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_ndcg_by_title_perfect_retrieval() -> None:
    gold = [{"title": "jordan-blake", "relevance": 2}]
    retrieved = ["entities/person/jordan-blake.md", "other/doc.md"]
    assert _ndcg_score_by_title(retrieved, gold, k=10) == pytest.approx(1.0)


@pytest.mark.unit
def test_ndcg_by_title_partial_retrieval() -> None:
    gold = [
        {"title": "jordan-blake", "relevance": 2},
        {"title": "team-overview", "relevance": 1},
    ]
    # Only second gold retrieved; first (highest relevance) not found → NDCG < 1
    retrieved = ["shared/team-overview.md", "other/doc.md"]
    score = _ndcg_score_by_title(retrieved, gold, k=10)
    assert 0.0 < score < 1.0


@pytest.mark.unit
def test_ndcg_by_title_miss() -> None:
    gold = [{"title": "jordan-blake", "relevance": 2}]
    retrieved = ["some/unrelated/doc.md"]
    assert _ndcg_score_by_title(retrieved, gold, k=10) == pytest.approx(0.0)


@pytest.mark.unit
def test_ndcg_by_title_empty_gold() -> None:
    assert _ndcg_score_by_title(["doc.md"], [], k=10) == pytest.approx(0.0)


@pytest.mark.unit
def test_ndcg_by_title_file_moved_still_matches() -> None:
    """Score is unaffected by vault reorganisation — title is the stable identity."""
    gold = [{"title": "patterns", "relevance": 2}]
    # Same note, different folder
    original_path = ["04-Agent-Knowledge/builder/patterns.md"]
    moved_path = ["Archive/old-knowledge/patterns.md"]
    assert _ndcg_score_by_title(original_path, gold, k=10) == pytest.approx(
        _ndcg_score_by_title(moved_path, gold, k=10)
    )


# ---------------------------------------------------------------------------
# _hit_at_k_by_title
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_hit_at_k_by_title_true() -> None:
    gold = [{"title": "jordan-blake", "relevance": 1}]
    assert _hit_at_k_by_title(["entities/person/jordan-blake.md"], gold, k=5) is True


@pytest.mark.unit
def test_hit_at_k_by_title_false_beyond_k() -> None:
    gold = [{"title": "jordan-blake", "relevance": 1}]
    paths = ["a.md", "b.md", "entities/person/jordan-blake.md"]
    assert _hit_at_k_by_title(paths, gold, k=2) is False


@pytest.mark.unit
def test_hit_at_k_by_title_excludes_zero_relevance() -> None:
    gold = [{"title": "jordan-blake", "relevance": 0}]
    assert _hit_at_k_by_title(["entities/person/jordan-blake.md"], gold, k=5) is False


# ---------------------------------------------------------------------------
# _reciprocal_rank_by_title
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_reciprocal_rank_by_title_first_position() -> None:
    gold = [{"title": "jordan-blake", "relevance": 1}]
    paths = ["entities/person/jordan-blake.md", "other.md"]
    assert _reciprocal_rank_by_title(paths, gold, k=10) == pytest.approx(1.0)


@pytest.mark.unit
def test_reciprocal_rank_by_title_second_position() -> None:
    gold = [{"title": "jordan-blake", "relevance": 1}]
    paths = ["other.md", "entities/person/jordan-blake.md"]
    assert _reciprocal_rank_by_title(paths, gold, k=10) == pytest.approx(0.5)


@pytest.mark.unit
def test_reciprocal_rank_by_title_not_found() -> None:
    gold = [{"title": "jordan-blake", "relevance": 1}]
    assert _reciprocal_rank_by_title(["unrelated.md"], gold, k=10) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# format_interpretation — NDCG display
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_format_interpretation_shows_ndcg_when_present() -> None:
    result = BenchmarkResult(
        meta={"suite_name": "test-suite", "system": "hybrid", "date": "2026-04-15", "n_cases": 5},
        summary={
            "weighted_total": 0.55,
            "category_scores": {"recall": 0.60, "entity": 0.70},
            "gates": {"phase1": False, "phase2": False, "phase3": False},
            "ndcg_at_10": 0.587,
            "hit_rate_at_5": 0.720,
            "mrr_at_10": 0.650,
        },
        diagnostics={},
        cases=[],
    )
    output = format_interpretation(result)
    assert "NDCG@10" in output
    assert "0.587" in output
    assert "Hit@5" in output
    assert "MRR@10" in output


@pytest.mark.unit
def test_format_interpretation_omits_ndcg_section_when_absent() -> None:
    result = BenchmarkResult(
        meta={"suite_name": "test-suite", "system": "hybrid", "date": "2026-04-15", "n_cases": 3},
        summary={
            "weighted_total": 0.70,
            "category_scores": {"recall": 0.75},
            "gates": {"phase1": True, "phase2": False, "phase3": False},
            "ndcg_at_10": None,
            "hit_rate_at_5": None,
            "mrr_at_10": None,
        },
        diagnostics={},
        cases=[],
    )
    output = format_interpretation(result)
    assert "NDCG@10" not in output
