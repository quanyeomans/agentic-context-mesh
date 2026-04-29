"""
Tests for kairix.agents.mcp.server — MCP tool implementations.

Tool functions are pure Python and importable without the ``mcp`` package.
All underlying kairix module calls are mocked.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from kairix.agents.mcp.server import tool_entity, tool_prep, tool_search, tool_timeline, tool_usage_guide

# ---------------------------------------------------------------------------
# tool_search
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_tool_search_returns_expected_shape() -> None:
    mock_budgeted = SimpleNamespace(
        result=SimpleNamespace(path="notes/foo.md", boosted_score=0.9),
        content="some text here",
        token_estimate=10,
    )
    mock_result = SimpleNamespace(
        query="test query",
        intent=SimpleNamespace(value="semantic"),
        results=[mock_budgeted],
        total_tokens=10,
        latency_ms=42.5,
        error="",
    )

    with patch("kairix.core.search.hybrid.search", return_value=mock_result):
        result = tool_search(query="test query", agent=None, scope="shared+agent", budget=3000)

    assert result["query"] == "test query"
    assert result["intent"] == "semantic"
    assert len(result["results"]) == 1
    assert result["results"][0]["path"] == "notes/foo.md"
    assert result["results"][0]["score"] == 0.9
    assert result["total_tokens"] == 10
    assert result["error"] == ""


@pytest.mark.unit
def test_tool_search_error_handled() -> None:
    with patch("kairix.core.search.hybrid.search", side_effect=RuntimeError("db unavailable")):
        result = tool_search(query="broken", agent=None, scope="shared", budget=3000)

    assert result["query"] == "broken"
    assert result["results"] == []
    assert "failed" in result["error"].lower()  # sanitised error, no internal details


@pytest.mark.unit
def test_tool_search_import_error_handled() -> None:
    with patch.dict("sys.modules", {"kairix.core.search.hybrid": None}):
        result = tool_search(query="broken", agent=None, scope="shared", budget=3000)

    assert result["results"] == []
    assert result["error"] != ""


@pytest.mark.unit
def test_tool_search_passes_agent_and_scope() -> None:
    mock_result = SimpleNamespace(
        query="q",
        intent=SimpleNamespace(value="entity"),
        results=[],
        total_tokens=0,
        latency_ms=1.0,
        error="",
    )
    with patch("kairix.core.search.hybrid.search", return_value=mock_result) as mock_search:
        tool_search(query="q", agent="builder", scope="agent", budget=1000)
        call_kwargs = mock_search.call_args.kwargs
        assert call_kwargs["query"] == "q"
        assert call_kwargs["agent"] == "builder"
        assert call_kwargs["scope"] == "agent"
        assert call_kwargs["budget"] == 1000
        # config is no longer passed explicitly — search() resolves per-collection config internally


@pytest.mark.unit
def test_tool_search_result_snippet_truncated() -> None:
    long_text = "x" * 1000
    mock_budgeted = SimpleNamespace(
        result=SimpleNamespace(path="a.md", boosted_score=0.5),
        content=long_text,
        token_estimate=50,
    )
    mock_result = SimpleNamespace(
        query="q",
        intent=SimpleNamespace(value="semantic"),
        results=[mock_budgeted],
        total_tokens=50,
        latency_ms=5.0,
        error="",
    )
    with patch("kairix.core.search.hybrid.search", return_value=mock_result):
        result = tool_search(query="q")

    assert len(result["results"][0]["snippet"]) == 500


# ---------------------------------------------------------------------------
# tool_entity
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_tool_entity_neo4j_primary() -> None:
    mock_neo4j = MagicMock()
    mock_neo4j.available = True
    mock_neo4j.cypher.return_value = [
        {
            "id": "acme",
            "name": "Acme",
            "type": "Organisation",
            "summary": "A health org",
            "vault_path": "02-Areas/00-Clients/Acme/Acme.md",
        }
    ]

    with patch("kairix.knowledge.graph.client.get_client", return_value=mock_neo4j):
        result = tool_entity(name="Acme")

    assert result["id"] == "acme"
    assert result["name"] == "Acme"
    assert result["type"] == "Organisation"
    assert result["error"] == ""


@pytest.mark.unit
def test_tool_entity_neo4j_not_found_returns_error() -> None:
    """When Neo4j returns empty results, entity not found error is returned."""
    mock_neo4j = MagicMock()
    mock_neo4j.available = True
    mock_neo4j.cypher.return_value = []  # not found in Neo4j

    with patch("kairix.knowledge.graph.client.get_client", return_value=mock_neo4j):
        result = tool_entity(name="Unknown Entity")

    assert result["id"] == ""
    assert "not found" in result["error"].lower()


@pytest.mark.unit
def test_tool_entity_neo4j_unavailable_returns_error() -> None:
    """When Neo4j is unavailable, entity not found is returned (no DB fallback)."""
    mock_neo4j = MagicMock()
    mock_neo4j.available = False

    with patch("kairix.knowledge.graph.client.get_client", return_value=mock_neo4j):
        result = tool_entity(name="Test")

    assert result["error"] != ""


@pytest.mark.unit
def test_tool_entity_neo4j_exception_returns_error() -> None:
    """When Neo4j raises, entity not found is returned."""
    with patch("kairix.knowledge.graph.client.get_client", side_effect=RuntimeError("no neo4j")):
        result = tool_entity(name="Anything")

    assert result["error"] != ""


# ---------------------------------------------------------------------------
# tool_prep
# ---------------------------------------------------------------------------


def _mock_search_result() -> MagicMock:
    """Create a mock SearchResult with budgeted results for prep tests."""
    sr = MagicMock()
    mock_result = MagicMock()
    mock_result.result.title = "test-doc"
    mock_result.result.path = "projects/test-doc.md"
    mock_result.content = "This is test document content about the topic."
    sr.results = [mock_result]
    return sr


@pytest.mark.unit
def test_tool_prep_l0() -> None:
    summary_text = "Brief context summary."
    with (
        patch("kairix.core.search.hybrid.search", return_value=_mock_search_result()),
        patch("kairix._azure.chat_completion", return_value=summary_text),
    ):
        result = tool_prep(query="What did we discuss last quarter?", tier="l0")

    assert result["tier"] == "l0"
    assert result["summary"] == summary_text
    assert result["error"] == ""
    assert "sources" in result


@pytest.mark.unit
def test_tool_prep_l1() -> None:
    summary_text = "Detailed context summary about the engagement."
    with (
        patch("kairix.core.search.hybrid.search", return_value=_mock_search_result()),
        patch("kairix._azure.chat_completion", return_value=summary_text),
    ):
        result = tool_prep(query="Explain our test engagement", tier="l1")

    assert result["tier"] == "l1"
    assert result["error"] == ""


@pytest.mark.unit
def test_tool_prep_no_results_returns_no_content() -> None:
    """Prep returns 'no relevant documents' when search finds nothing."""
    empty_sr = MagicMock()
    empty_sr.results = []
    with patch("kairix.core.search.hybrid.search", return_value=empty_sr):
        result = tool_prep(query="something obscure", tier="l0")

    assert "no relevant documents" in result["summary"].lower()
    assert result["error"] == ""


@pytest.mark.unit
def test_tool_prep_error_handled() -> None:
    with patch("kairix.core.search.hybrid.search", side_effect=RuntimeError("search unavailable")):
        result = tool_prep(query="anything", tier="l0")

    assert result["summary"] == ""
    assert "failed" in result["error"].lower()


@pytest.mark.unit
def test_tool_prep_default_tier_is_l0() -> None:
    with (
        patch("kairix.core.search.hybrid.search", return_value=_mock_search_result()),
        patch("kairix._azure.chat_completion", return_value="ok") as mock_chat,
    ):
        tool_prep(query="q")
        mock_chat.assert_called_once()
        _, kwargs = mock_chat.call_args
        assert kwargs.get("max_tokens") == 150


# ---------------------------------------------------------------------------
# tool_timeline
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_tool_timeline_temporal_query() -> None:
    rewritten = "what happened 2026-04-06..2026-04-13"
    with patch("kairix.core.temporal.rewriter.is_relative_temporal", return_value=True):
        with patch("kairix.core.temporal.rewriter.rewrite_temporal_query", return_value=rewritten):
            with patch(
                "kairix.core.temporal.rewriter.extract_time_window",
                return_value=("2026-04-06", "2026-04-13"),
            ):
                result = tool_timeline(query="what happened last week")

    assert result["is_temporal"] is True
    assert result["rewritten_query"] == "what happened 2026-04-06..2026-04-13"
    assert result["time_window"]["start"] == "2026-04-06"
    assert result["time_window"]["end"] == "2026-04-13"
    assert result["error"] == ""


@pytest.mark.unit
def test_tool_timeline_non_temporal_query() -> None:
    with patch("kairix.core.temporal.rewriter.is_relative_temporal", return_value=False):
        result = tool_timeline(query="tell me about Acme")

    assert result["is_temporal"] is False
    assert result["rewritten_query"] == "tell me about Acme"
    assert result["time_window"] == {}
    assert result["error"] == ""


@pytest.mark.unit
def test_tool_timeline_preserves_original_query() -> None:
    with patch("kairix.core.temporal.rewriter.is_relative_temporal", return_value=False):
        result = tool_timeline(query="original question here")

    assert result["original_query"] == "original question here"
    assert result["rewritten_query"] == "original question here"


@pytest.mark.unit
def test_tool_timeline_error_handled() -> None:
    """When extract_time_window fails, timeline gracefully returns non-temporal result."""
    with patch("kairix.core.temporal.rewriter.extract_time_window", side_effect=RuntimeError("oops")):
        result = tool_timeline(query="any query")

    assert result["is_temporal"] is False
    assert result["rewritten_query"] == "any query"
    assert result["error"] == ""  # graceful degradation, not an error


@pytest.mark.unit
def test_tool_timeline_rewrite_none_returns_original() -> None:
    with (
        patch("kairix.core.temporal.rewriter.extract_time_window", return_value=(None, None)),
        patch("kairix.core.temporal.rewriter.rewrite_temporal_query", return_value=None),
    ):
        result = tool_timeline(query="last month update")

    assert result["rewritten_query"] == "last month update"


# ---------------------------------------------------------------------------
# build_server — ImportError when mcp not installed
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_build_server_raises_when_mcp_not_installed() -> None:
    import sys

    with patch.dict(sys.modules, {"mcp": None, "mcp.server": None, "mcp.server.fastmcp": None}):
        from kairix.agents.mcp.server import build_server

        with pytest.raises(ImportError, match="mcp"):
            build_server()


# ---------------------------------------------------------------------------
# tool_usage_guide (TEST-5)
# ---------------------------------------------------------------------------


@pytest.fixture()
def guide_file(tmp_path: Path) -> Path:
    """Create a temporary agent-usage-guide.md."""
    guide = tmp_path / "agent-usage-guide.md"
    guide.write_text(
        "# Kairix Agent Usage Guide\n\n"
        "## Search\nHow to search the document store.\n\n"
        "## Budget\nToken budget controls cost.\nDefault budget is 3000 tokens.\n\n"
        "## Troubleshooting\nDebug tips for common issues.\n",
        encoding="utf-8",
    )
    return guide


@pytest.mark.unit
def test_tool_usage_guide_empty_topic(guide_file: Path) -> None:
    """Empty topic returns full guide content."""
    import kairix.agents.mcp.server as _mod

    server_file = Path(_mod.__file__)
    expected = server_file.parent.parent.parent / "docs" / "agent-usage-guide.md"
    if expected.exists():
        result = tool_usage_guide(topic="")
        assert result["error"] == ""
        assert len(result["content"]) > 0
    else:
        expected.parent.mkdir(parents=True, exist_ok=True)
        expected.write_text(guide_file.read_text(), encoding="utf-8")
        try:
            result = tool_usage_guide(topic="")
            assert result["error"] == ""
            assert "Kairix Agent Usage Guide" in result["content"]
        finally:
            expected.unlink(missing_ok=True)


@pytest.mark.unit
def test_tool_usage_guide_topic_filter(guide_file: Path) -> None:
    """Specific topic filters to relevant sections."""
    import kairix.agents.mcp.server as _mod

    server_file = Path(_mod.__file__)
    expected = server_file.parent.parent.parent / "docs" / "agent-usage-guide.md"
    if expected.exists():
        result = tool_usage_guide(topic="budget")
        assert result["error"] == ""
        assert "budget" in result["content"].lower()
    else:
        expected.parent.mkdir(parents=True, exist_ok=True)
        expected.write_text(guide_file.read_text(), encoding="utf-8")
        try:
            result = tool_usage_guide(topic="budget")
            assert result["error"] == ""
            assert "budget" in result["content"].lower()
        finally:
            expected.unlink(missing_ok=True)


@pytest.mark.unit
def test_tool_usage_guide_missing_file(tmp_path: Path, monkeypatch) -> None:
    """Missing guide file returns error dict."""
    import kairix as _kairix_pkg
    import kairix.agents.mcp.server as _mod

    monkeypatch.setattr(_mod, "__file__", str(tmp_path / "kairix" / "mcp" / "server.py"))
    monkeypatch.setattr(_kairix_pkg, "__file__", str(tmp_path / "kairix" / "__init__.py"))
    result = tool_usage_guide(topic="anything")
    assert result["error"] != ""
    assert result["content"] == ""
