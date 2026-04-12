"""
Tests for kairix.mcp.server — MCP tool implementations.

Tool functions are pure Python and importable without the ``mcp`` package.
All underlying kairix module calls are mocked.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from kairix.mcp.server import tool_entity, tool_prep, tool_search, tool_timeline


# ---------------------------------------------------------------------------
# tool_search
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_tool_search_returns_expected_shape() -> None:
    mock_result = SimpleNamespace(
        query="test query",
        intent=SimpleNamespace(value="semantic"),
        results=[
            SimpleNamespace(path="notes/foo.md", score=0.9, text="some text here", tokens=10)
        ],
        total_tokens=10,
        latency_ms=42.5,
        error="",
    )

    with patch("kairix.mcp.server.tool_search") as _:
        # Test the logic directly by calling with a mocked search
        pass

    with patch("kairix.search.hybrid.search", return_value=mock_result):
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
    with patch("kairix.search.hybrid.search", side_effect=RuntimeError("db unavailable")):
        result = tool_search(query="broken", agent=None, scope="shared", budget=3000)

    assert result["query"] == "broken"
    assert result["results"] == []
    assert "db unavailable" in result["error"]


@pytest.mark.unit
def test_tool_search_import_error_handled() -> None:
    with patch.dict("sys.modules", {"kairix.search.hybrid": None}):
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
    with patch("kairix.search.hybrid.search", return_value=mock_result) as mock_search:
        tool_search(query="q", agent="shape", scope="agent", budget=1000)
        mock_search.assert_called_once_with(query="q", agent="shape", scope="agent", budget=1000)


@pytest.mark.unit
def test_tool_search_result_snippet_truncated() -> None:
    long_text = "x" * 1000
    mock_result = SimpleNamespace(
        query="q",
        intent=SimpleNamespace(value="semantic"),
        results=[SimpleNamespace(path="a.md", score=0.5, text=long_text, tokens=50)],
        total_tokens=50,
        latency_ms=5.0,
        error="",
    )
    with patch("kairix.search.hybrid.search", return_value=mock_result):
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
        {"id": "bupa", "name": "Bupa", "type": "Organisation", "summary": "A health org", "vault_path": "02-Areas/00-Clients/Bupa/Bupa.md"}
    ]

    with patch("kairix.graph.client.get_client", return_value=mock_neo4j):
        result = tool_entity(name="Bupa")

    assert result["id"] == "bupa"
    assert result["name"] == "Bupa"
    assert result["type"] == "Organisation"
    assert result["error"] == ""


@pytest.mark.unit
def test_tool_entity_neo4j_not_found_falls_back() -> None:
    mock_neo4j = MagicMock()
    mock_neo4j.available = True
    mock_neo4j.cypher.return_value = []  # not found in Neo4j

    mock_entity = SimpleNamespace(
        id=42,
        name="Felicity Herron",
        entity_type="person",
        summary="CTO at Bupa",
        vault_path="02-Areas/Network/People-Notes/felicity-herron.md",
    )

    with patch("kairix.graph.client.get_client", return_value=mock_neo4j):
        with patch("kairix.entities.graph.entity_lookup", return_value=mock_entity):
            with patch("kairix.entities.schema.open_entities_db", return_value=MagicMock()):
                result = tool_entity(name="Felicity Herron")

    assert result["name"] == "Felicity Herron"
    assert result["error"] == ""


@pytest.mark.unit
def test_tool_entity_not_found_returns_error() -> None:
    mock_neo4j = MagicMock()
    mock_neo4j.available = True
    mock_neo4j.cypher.return_value = []

    with patch("kairix.graph.client.get_client", return_value=mock_neo4j):
        with patch("kairix.entities.graph.entity_lookup", return_value=None):
            with patch("kairix.entities.schema.open_entities_db", return_value=MagicMock()):
                result = tool_entity(name="Unknown Entity")

    assert result["id"] == ""
    assert "not found" in result["error"].lower()


@pytest.mark.unit
def test_tool_entity_neo4j_unavailable_falls_back() -> None:
    mock_neo4j = MagicMock()
    mock_neo4j.available = False

    mock_entity = SimpleNamespace(id=1, name="Test", entity_type="concept", summary="", vault_path="")

    with patch("kairix.graph.client.get_client", return_value=mock_neo4j):
        with patch("kairix.entities.graph.entity_lookup", return_value=mock_entity):
            with patch("kairix.entities.schema.open_entities_db", return_value=MagicMock()):
                result = tool_entity(name="Test")

    assert result["name"] == "Test"
    assert result["error"] == ""


@pytest.mark.unit
def test_tool_entity_total_failure_returns_error() -> None:
    with patch("kairix.graph.client.get_client", side_effect=RuntimeError("no neo4j")):
        with patch("kairix.entities.schema.open_entities_db", side_effect=RuntimeError("no db")):
            result = tool_entity(name="Anything")

    assert result["error"] != ""


# ---------------------------------------------------------------------------
# tool_prep
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_tool_prep_l0() -> None:
    mock_result = SimpleNamespace(summary="Brief context summary.", tokens=42)

    with patch("kairix.summaries.generate.generate_l0", return_value=mock_result):
        result = tool_prep(query="What did we discuss last quarter?", tier="l0")

    assert result["tier"] == "l0"
    assert result["summary"] == "Brief context summary."
    assert result["tokens"] == 42
    assert result["error"] == ""


@pytest.mark.unit
def test_tool_prep_l1() -> None:
    mock_result = SimpleNamespace(summary="Detailed context summary.", tokens=800)

    with patch("kairix.summaries.generate.generate_l1", return_value=mock_result):
        result = tool_prep(query="Explain our Bupa engagement", tier="l1")

    assert result["tier"] == "l1"
    assert result["tokens"] == 800
    assert result["error"] == ""


@pytest.mark.unit
def test_tool_prep_error_handled() -> None:
    with patch("kairix.summaries.generate.generate_l0", side_effect=RuntimeError("llm unavailable")):
        result = tool_prep(query="anything", tier="l0")

    assert result["summary"] == ""
    assert "llm unavailable" in result["error"]


@pytest.mark.unit
def test_tool_prep_default_tier_is_l0() -> None:
    mock_result = SimpleNamespace(summary="ok", tokens=10)

    with patch("kairix.summaries.generate.generate_l0", return_value=mock_result) as mock_l0:
        with patch("kairix.summaries.generate.generate_l1") as mock_l1:
            tool_prep(query="q")
            mock_l0.assert_called_once()
            mock_l1.assert_not_called()


# ---------------------------------------------------------------------------
# tool_timeline
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_tool_timeline_temporal_query() -> None:
    with patch("kairix.temporal.rewriter.is_relative_temporal", return_value=True):
        with patch("kairix.temporal.rewriter.rewrite_temporal_query", return_value="what happened 2026-04-06..2026-04-13"):
            with patch("kairix.temporal.rewriter.extract_time_window", return_value=SimpleNamespace(start="2026-04-06", end="2026-04-13")):
                result = tool_timeline(query="what happened last week")

    assert result["is_temporal"] is True
    assert result["rewritten_query"] == "what happened 2026-04-06..2026-04-13"
    assert result["time_window"]["start"] == "2026-04-06"
    assert result["time_window"]["end"] == "2026-04-13"
    assert result["error"] == ""


@pytest.mark.unit
def test_tool_timeline_non_temporal_query() -> None:
    with patch("kairix.temporal.rewriter.is_relative_temporal", return_value=False):
        result = tool_timeline(query="tell me about Bupa")

    assert result["is_temporal"] is False
    assert result["rewritten_query"] == "tell me about Bupa"
    assert result["time_window"] == {}
    assert result["error"] == ""


@pytest.mark.unit
def test_tool_timeline_preserves_original_query() -> None:
    with patch("kairix.temporal.rewriter.is_relative_temporal", return_value=False):
        result = tool_timeline(query="original question here")

    assert result["original_query"] == "original question here"
    assert result["rewritten_query"] == "original question here"


@pytest.mark.unit
def test_tool_timeline_error_handled() -> None:
    with patch("kairix.temporal.rewriter.is_relative_temporal", side_effect=RuntimeError("oops")):
        result = tool_timeline(query="any query")

    assert result["is_temporal"] is False
    assert result["rewritten_query"] == "any query"
    assert "oops" in result["error"]


@pytest.mark.unit
def test_tool_timeline_rewrite_none_returns_original() -> None:
    with patch("kairix.temporal.rewriter.is_relative_temporal", return_value=True):
        with patch("kairix.temporal.rewriter.rewrite_temporal_query", return_value=None):
            with patch("kairix.temporal.rewriter.extract_time_window", return_value=None):
                result = tool_timeline(query="last month update")

    assert result["rewritten_query"] == "last month update"


# ---------------------------------------------------------------------------
# build_server — ImportError when mcp not installed
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_build_server_raises_when_mcp_not_installed() -> None:
    import sys

    with patch.dict(sys.modules, {"mcp": None, "mcp.server": None, "mcp.server.fastmcp": None}):
        from kairix.mcp.server import build_server

        with pytest.raises(ImportError, match="mcp"):
            build_server()
