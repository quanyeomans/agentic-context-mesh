"""
Tests for mnemosyne.search.planner — QueryPlanner decompose + retrieve_and_merge.

Tests cover:
  - decompose() fallback when chat_completion import fails
  - decompose() fallback when JSON parse fails
  - decompose() fallback when response is empty
  - decompose() fallback when result list is invalid
  - decompose() success: single sub-query passthrough
  - decompose() success: multi-hop decomposition (3 sub-queries)
  - decompose() with entities_db when graph context is unavailable
  - decompose() with entities_db when graph context is available
  - retrieve_and_merge() single sub-query, RRF merge
  - retrieve_and_merge() multiple sub-queries, RRF merge and deduplication
  - retrieve_and_merge() search_fn raises exception → returns what succeeded
  - retrieve_and_merge() empty sub-queries → empty results
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

from mnemosyne.search.planner import QueryPlanner

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class _FakeResult:
    """Minimal fake result with .path attribute for retrieve_and_merge."""

    path: str
    score: float = 1.0


def _search_fn_factory(results_by_query: dict[str, list[_FakeResult]]):
    """Return a search function that maps queries to pre-set results."""

    def search_fn(query: str) -> list[_FakeResult]:
        return results_by_query.get(query, [])

    return search_fn


# ---------------------------------------------------------------------------
# decompose() tests
# ---------------------------------------------------------------------------


class TestDecompose:
    def test_fallback_when_import_fails(self) -> None:
        """Should return [query] when mnemosyne._azure is not importable."""
        planner = QueryPlanner()
        with patch("builtins.__import__", side_effect=ImportError("no module")):
            result = planner.decompose("what is the meaning of life?")
        assert result == ["what is the meaning of life?"]

    def test_fallback_when_chat_completion_raises(self) -> None:
        """Should return [query] when chat_completion raises."""
        planner = QueryPlanner()
        mock_azure = MagicMock()
        mock_azure.chat_completion.side_effect = RuntimeError("API down")
        with patch.dict("sys.modules", {"mnemosyne._azure": mock_azure}):
            result = planner.decompose("query that fails")
        assert result == ["query that fails"]

    def test_fallback_when_response_is_empty(self) -> None:
        """Should return [query] when chat_completion returns empty string."""
        planner = QueryPlanner()
        mock_azure = MagicMock()
        mock_azure.chat_completion.return_value = ""
        with patch.dict("sys.modules", {"mnemosyne._azure": mock_azure}):
            result = planner.decompose("what happened in march")
        assert result == ["what happened in march"]

    def test_fallback_when_json_invalid(self) -> None:
        """Should return [query] when response is not valid JSON."""
        planner = QueryPlanner()
        mock_azure = MagicMock()
        mock_azure.chat_completion.return_value = "not json at all"
        with patch.dict("sys.modules", {"mnemosyne._azure": mock_azure}):
            result = planner.decompose("some query")
        assert result == ["some query"]

    def test_fallback_when_json_not_list(self) -> None:
        """Should return [query] when response JSON is not a list."""
        planner = QueryPlanner()
        mock_azure = MagicMock()
        mock_azure.chat_completion.return_value = '{"key": "value"}'
        with patch.dict("sys.modules", {"mnemosyne._azure": mock_azure}):
            result = planner.decompose("what")
        assert result == ["what"]

    def test_fallback_when_list_too_long(self) -> None:
        """Should return [query] when response list has more than 3 items."""
        planner = QueryPlanner()
        mock_azure = MagicMock()
        mock_azure.chat_completion.return_value = '["a", "b", "c", "d"]'
        with patch.dict("sys.modules", {"mnemosyne._azure": mock_azure}):
            result = planner.decompose("something")
        assert result == ["something"]

    def test_fallback_when_list_is_empty(self) -> None:
        """Should return [query] when response list is empty."""
        planner = QueryPlanner()
        mock_azure = MagicMock()
        mock_azure.chat_completion.return_value = "[]"
        with patch.dict("sys.modules", {"mnemosyne._azure": mock_azure}):
            result = planner.decompose("empty response")
        assert result == ["empty response"]

    def test_success_single_sub_query(self) -> None:
        """Should return the single sub-query from a simple query."""
        planner = QueryPlanner()
        mock_azure = MagicMock()
        mock_azure.chat_completion.return_value = '["simple query passthrough"]'
        with patch.dict("sys.modules", {"mnemosyne._azure": mock_azure}):
            result = planner.decompose("simple query passthrough")
        assert result == ["simple query passthrough"]

    def test_success_multi_hop_three_subs(self) -> None:
        """Should return 3 sub-queries for a complex multi-hop query."""
        planner = QueryPlanner()
        mock_azure = MagicMock()
        mock_azure.chat_completion.return_value = '["sub1", "sub2", "sub3"]'
        with patch.dict("sys.modules", {"mnemosyne._azure": mock_azure}):
            result = planner.decompose("complex query needing decomposition")
        assert result == ["sub1", "sub2", "sub3"]

    def test_filters_empty_strings_from_list(self) -> None:
        """Should filter out empty strings from the sub-query list."""
        planner = QueryPlanner()
        mock_azure = MagicMock()
        mock_azure.chat_completion.return_value = '["sub1", "", "sub2"]'
        with patch.dict("sys.modules", {"mnemosyne._azure": mock_azure}):
            result = planner.decompose("query with blanks")
        assert result == ["sub1", "sub2"]

    def test_with_entities_db_graph_unavailable(self) -> None:
        """Should use plain prompt when graph context import fails."""
        planner = QueryPlanner()
        mock_azure = MagicMock()
        mock_azure.chat_completion.return_value = '["query"]'
        db = sqlite3.connect(":memory:")

        with patch.dict("sys.modules", {"mnemosyne._azure": mock_azure}):
            with patch(
                "mnemosyne.search.planner.QueryPlanner.decompose",
                wraps=planner.decompose,
            ):
                # graph_context import will fail since mnemosyne.entities.graph
                # is not fully set up — should fall back to plain prompt
                result = planner.decompose("active projects avanade", entities_db=db)

        # Should still return a list (either from graph or plain prompt)
        assert isinstance(result, list)
        assert len(result) >= 1
        db.close()

    def test_with_entities_db_graph_context_injected(self) -> None:
        """Should inject entity context when graph_context returns non-empty string."""
        planner = QueryPlanner()
        mock_azure = MagicMock()
        mock_azure.chat_completion.return_value = '["entity-aware sub-query"]'
        db = sqlite3.connect(":memory:")

        mock_graph = MagicMock()
        mock_graph.graph_context.return_value = "Entity: Avanade is a consulting firm."

        with patch.dict(
            "sys.modules",
            {
                "mnemosyne._azure": mock_azure,
                "mnemosyne.entities.graph": mock_graph,
            },
        ):
            result = planner.decompose("what is avanade doing", entities_db=db)

        assert result == ["entity-aware sub-query"]
        # Confirm the call included entity context in the prompt
        call_args = mock_azure.chat_completion.call_args
        prompt_content = call_args[1]["messages"][0]["content"]
        assert "Entity" in prompt_content
        db.close()


# ---------------------------------------------------------------------------
# retrieve_and_merge() tests
# ---------------------------------------------------------------------------


class TestRetrieveAndMerge:
    def test_single_sub_query_returns_results(self) -> None:
        """Single sub-query: results returned in order."""
        planner = QueryPlanner()
        results = [_FakeResult(path=f"doc{i}.md") for i in range(5)]
        search_fn = _search_fn_factory({"query1": results})

        merged = planner.retrieve_and_merge(["query1"], search_fn, top_k_per_sub=5, final_top_k=5)
        assert len(merged) == 5
        paths = [r.path for r in merged]
        for i in range(5):
            assert f"doc{i}.md" in paths

    def test_multi_sub_query_deduplication(self) -> None:
        """Same document in multiple sub-query results → appears once in merged."""
        planner = QueryPlanner()
        shared = _FakeResult(path="shared.md")
        only_q1 = _FakeResult(path="only-q1.md")
        only_q2 = _FakeResult(path="only-q2.md")

        search_fn = _search_fn_factory(
            {
                "q1": [shared, only_q1],
                "q2": [shared, only_q2],
            }
        )

        merged = planner.retrieve_and_merge(["q1", "q2"], search_fn, top_k_per_sub=5, final_top_k=6)
        paths = [r.path for r in merged]

        # shared.md must appear exactly once
        assert paths.count("shared.md") == 1
        assert "only-q1.md" in paths
        assert "only-q2.md" in paths

    def test_rrf_boosts_document_in_multiple_lists(self) -> None:
        """Document appearing in both sub-query result lists should rank higher."""
        planner = QueryPlanner()
        shared = _FakeResult(path="shared.md")
        unique1 = _FakeResult(path="unique1.md")
        unique2 = _FakeResult(path="unique2.md")

        search_fn = _search_fn_factory(
            {
                "q1": [unique1, shared],
                "q2": [unique2, shared],
            }
        )

        merged = planner.retrieve_and_merge(["q1", "q2"], search_fn, top_k_per_sub=5, final_top_k=6)
        paths = [r.path for r in merged]

        # shared.md appears in both lists at rank 2 → RRF score higher than unique docs at rank 2
        assert paths[0] == "shared.md", f"Expected shared.md first, got {paths}"

    def test_search_fn_exception_handled(self) -> None:
        """If search_fn raises for one sub-query, other results still returned."""
        planner = QueryPlanner()
        good_results = [_FakeResult(path="good.md")]

        def flaky_search(query: str) -> list[Any]:
            if query == "bad":
                raise RuntimeError("search failed")
            return good_results

        merged = planner.retrieve_and_merge(["good", "bad"], flaky_search, top_k_per_sub=5, final_top_k=6)
        paths = [r.path for r in merged]
        assert "good.md" in paths

    def test_respects_final_top_k(self) -> None:
        """Result count should not exceed final_top_k."""
        planner = QueryPlanner()
        results = [_FakeResult(path=f"doc{i}.md") for i in range(10)]
        search_fn = _search_fn_factory({"q1": results[:5], "q2": results[5:]})

        merged = planner.retrieve_and_merge(["q1", "q2"], search_fn, top_k_per_sub=5, final_top_k=3)
        assert len(merged) <= 3

    def test_dict_results_with_file_key(self) -> None:
        """Handles dict results with 'file' key (BM25-style)."""
        planner = QueryPlanner()
        results = [{"file": "bm25-doc.md", "score": 1.0}]
        search_fn = _search_fn_factory({"q": results})

        merged = planner.retrieve_and_merge(["q"], search_fn, top_k_per_sub=5, final_top_k=5)
        assert len(merged) == 1

    def test_dict_results_with_path_key(self) -> None:
        """Handles dict results with 'path' key."""
        planner = QueryPlanner()
        results = [{"path": "path-doc.md", "score": 1.0}]
        search_fn = _search_fn_factory({"q": results})

        merged = planner.retrieve_and_merge(["q"], search_fn, top_k_per_sub=5, final_top_k=5)
        assert len(merged) == 1

    def test_nested_result_path_attribute(self) -> None:
        """Handles results with .result.path (BudgetedResult style)."""
        planner = QueryPlanner()

        inner = MagicMock()
        inner.path = "nested-doc.md"
        outer = MagicMock()
        outer.result = inner
        # Ensure hasattr checks pass
        del outer.path  # no direct .path on outer

        results = [outer]
        search_fn = _search_fn_factory({"q": results})

        merged = planner.retrieve_and_merge(["q"], search_fn, top_k_per_sub=5, final_top_k=5)
        assert len(merged) == 1

    def test_top_k_per_sub_limits_results_per_sub(self) -> None:
        """top_k_per_sub limits how many results per sub-query enter RRF."""
        planner = QueryPlanner()
        results = [_FakeResult(path=f"doc{i}.md") for i in range(10)]
        search_fn = _search_fn_factory({"q": results})

        # With top_k_per_sub=2, only doc0 and doc1 should enter RRF
        merged = planner.retrieve_and_merge(["q"], search_fn, top_k_per_sub=2, final_top_k=10)
        paths = [r.path for r in merged]
        assert len(paths) == 2
        assert "doc0.md" in paths
        assert "doc1.md" in paths
