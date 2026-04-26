"""Tests for cross-encoder re-ranking module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import kairix.search.rerank as rerank_mod
from kairix.search.rerank import RERANK_CANDIDATE_LIMIT, RERANK_MODEL, _get_cross_encoder, rerank
from kairix.search.rrf import FusedResult


def _make_result(path: str, score: float, snippet: str = "") -> FusedResult:
    return FusedResult(
        path=path,
        collection="test",
        title=path,
        snippet=snippet or f"Snippet for {path}",
        rrf_score=score,
        boosted_score=score,
    )


@pytest.mark.unit
class TestRerank:
    @pytest.mark.unit
    def test_returns_unchanged_when_sentence_transformers_not_installed(self):
        results = [_make_result("a.md", 0.9), _make_result("b.md", 0.5)]
        with patch("kairix.search.rerank._get_cross_encoder", return_value=None):
            out = rerank("test query", results)
        assert out == results  # same objects, same order

    @pytest.mark.unit
    def test_reorders_by_cross_encoder_score(self):
        results = [
            _make_result("a.md", 0.9, snippet="irrelevant content"),
            _make_result("b.md", 0.5, snippet="highly relevant content"),
        ]
        mock_encoder = MagicMock()
        # Return scores as list (simulates numpy array .tolist() result via our code)
        mock_encoder.predict.return_value = MagicMock(tolist=lambda: [0.1, 0.9])

        with patch("kairix.search.rerank._get_cross_encoder", return_value=mock_encoder):
            out = rerank("highly relevant query", results)

        assert out[0].path == "b.md"
        assert out[1].path == "a.md"

    @pytest.mark.unit
    def test_overwrites_boosted_score(self):
        results = [_make_result("a.md", 0.9), _make_result("b.md", 0.1)]
        mock_encoder = MagicMock()
        mock_encoder.predict.return_value = MagicMock(tolist=lambda: [3.0, 7.0])

        with patch("kairix.search.rerank._get_cross_encoder", return_value=mock_encoder):
            out = rerank("query", results)

        # b.md should now be first with boosted_score = 7.0
        assert out[0].path == "b.md"
        assert out[0].boosted_score == pytest.approx(7.0)

    @pytest.mark.unit
    def test_tail_results_appended_unchanged(self):
        many = [_make_result(f"{i}.md", float(i)) for i in range(25)]
        scores = list(range(RERANK_CANDIDATE_LIMIT))
        mock_encoder = MagicMock()
        mock_encoder.predict.return_value = MagicMock(tolist=lambda: [float(s) for s in scores])

        with patch("kairix.search.rerank._get_cross_encoder", return_value=mock_encoder):
            out = rerank("query", many, candidate_limit=RERANK_CANDIDATE_LIMIT)

        # All 25 results returned (top 20 re-ranked, remaining 5 appended)
        assert len(out) == 25
        # Tail results are beyond candidate_limit — paths from original index 20-24
        tail_paths = {r.path for r in out[RERANK_CANDIDATE_LIMIT:]}
        expected_tail = {f"{i}.md" for i in range(RERANK_CANDIDATE_LIMIT, 25)}
        assert tail_paths == expected_tail

    @pytest.mark.unit
    def test_returns_unchanged_on_inference_error(self):
        results = [_make_result("a.md", 0.9), _make_result("b.md", 0.5)]
        mock_encoder = MagicMock()
        mock_encoder.predict.side_effect = RuntimeError("inference failed")

        with patch("kairix.search.rerank._get_cross_encoder", return_value=mock_encoder):
            out = rerank("query", results)

        assert out == results

    @pytest.mark.unit
    def test_empty_results_returned_unchanged(self):
        with patch("kairix.search.rerank._get_cross_encoder", return_value=MagicMock()):
            out = rerank("query", [])
        assert out == []

    @pytest.mark.unit
    def test_rerank_score_field_populated(self):
        results = [_make_result("a.md", 0.5)]
        mock_encoder = MagicMock()
        mock_encoder.predict.return_value = MagicMock(tolist=lambda: [4.2])

        with patch("kairix.search.rerank._get_cross_encoder", return_value=mock_encoder):
            out = rerank("query", results)

        assert out[0].rerank_score == pytest.approx(4.2)

    @pytest.mark.unit
    def test_snippet_truncated_to_500_chars(self):
        """Snippet passed to cross-encoder is truncated to 500 characters."""
        long_snippet = "x" * 1000
        results = [_make_result("a.md", 0.5, snippet=long_snippet)]
        mock_encoder = MagicMock()
        mock_encoder.predict.return_value = MagicMock(tolist=lambda: [1.0])

        with patch("kairix.search.rerank._get_cross_encoder", return_value=mock_encoder):
            rerank("query", results)

        call_args = mock_encoder.predict.call_args[0][0]
        # The snippet in the pair should be at most 500 chars
        assert len(call_args[0][1]) == 500

    @pytest.mark.unit
    def test_uses_title_when_snippet_empty(self):
        """When snippet is empty/falsy, title is used instead."""
        result = FusedResult(
            path="doc.md",
            collection="test",
            title="doc.md",
            snippet="",
            rrf_score=0.5,
            boosted_score=0.5,
        )
        mock_encoder = MagicMock()
        mock_encoder.predict.return_value = MagicMock(tolist=lambda: [2.0])

        with patch("kairix.search.rerank._get_cross_encoder", return_value=mock_encoder):
            rerank("query", [result])

        call_args = mock_encoder.predict.call_args[0][0]
        assert call_args[0][1] == "doc.md"  # title is used

    @pytest.mark.unit
    def test_single_result_reranked(self):
        """Single result is still processed through reranker."""
        results = [_make_result("only.md", 0.3)]
        mock_encoder = MagicMock()
        mock_encoder.predict.return_value = MagicMock(tolist=lambda: [5.5])

        with patch("kairix.search.rerank._get_cross_encoder", return_value=mock_encoder):
            out = rerank("query", results)

        assert len(out) == 1
        assert out[0].rerank_score == pytest.approx(5.5)
        assert out[0].boosted_score == pytest.approx(5.5)

    @pytest.mark.unit
    def test_custom_candidate_limit(self):
        """Custom candidate_limit controls how many results are re-scored."""
        results = [_make_result(f"{i}.md", float(i)) for i in range(10)]
        mock_encoder = MagicMock()
        mock_encoder.predict.return_value = MagicMock(tolist=lambda: [float(i) for i in range(3)])

        with patch("kairix.search.rerank._get_cross_encoder", return_value=mock_encoder):
            out = rerank("query", results, candidate_limit=3)

        assert len(out) == 10
        # Encoder should have been called with only 3 pairs
        call_args = mock_encoder.predict.call_args[0][0]
        assert len(call_args) == 3

    @pytest.mark.unit
    def test_negative_scores_handled(self):
        """Cross-encoder can return negative scores; they should still sort correctly."""
        results = [
            _make_result("a.md", 0.9),
            _make_result("b.md", 0.5),
        ]
        mock_encoder = MagicMock()
        mock_encoder.predict.return_value = MagicMock(tolist=lambda: [-2.0, -0.5])

        with patch("kairix.search.rerank._get_cross_encoder", return_value=mock_encoder):
            out = rerank("query", results)

        # b.md has higher score (-0.5 > -2.0)
        assert out[0].path == "b.md"
        assert out[1].path == "a.md"


@pytest.mark.unit
class TestGetCrossEncoder:
    @pytest.mark.unit
    def test_returns_none_when_import_fails(self):
        """_get_cross_encoder returns None when sentence_transformers is not installed."""
        # Reset singleton
        rerank_mod._cross_encoder = None
        with patch.dict("sys.modules", {"sentence_transformers": None}):
            with patch("builtins.__import__", side_effect=ImportError("no module")):
                result = _get_cross_encoder("some-model")
        assert result is None
        rerank_mod._cross_encoder = None  # cleanup

    @pytest.mark.unit
    def test_returns_cached_singleton(self):
        """_get_cross_encoder returns cached instance on second call."""
        mock_encoder = MagicMock()
        rerank_mod._cross_encoder = mock_encoder
        try:
            result = _get_cross_encoder("any-model")
            assert result is mock_encoder
        finally:
            rerank_mod._cross_encoder = None

    @pytest.mark.unit
    def test_returns_none_on_load_exception(self):
        """_get_cross_encoder returns None on model load exception."""
        rerank_mod._cross_encoder = None

        mock_st = MagicMock()
        mock_st.CrossEncoder.side_effect = RuntimeError("model load failed")

        with patch.dict("sys.modules", {"sentence_transformers": mock_st}):
            result = _get_cross_encoder("bad-model")

        assert result is None
        rerank_mod._cross_encoder = None

    @pytest.mark.unit
    def test_default_model_constant(self):
        """RERANK_MODEL is the expected default."""
        assert RERANK_MODEL == "cross-encoder/ms-marco-MiniLM-L-6-v2"

    @pytest.mark.unit
    def test_default_candidate_limit(self):
        """RERANK_CANDIDATE_LIMIT is 20."""
        assert RERANK_CANDIDATE_LIMIT == 20
