"""Tests for cross-encoder re-ranking module."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from kairix.search.rrf import FusedResult
from kairix.search.rerank import rerank, RERANK_CANDIDATE_LIMIT


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
