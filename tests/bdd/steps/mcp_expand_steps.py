"""Step definitions for mcp_expand.feature.

F46-clean: every scenario composes through the public MCP tool handler
(``kairix.agents.mcp.server.tool_expand``) with deps injected through the
public seam — the canonical ``FakeDocumentRepository.get_by_path`` chunk
reader. No direct pipeline construction, no monkeypatching (F1), no env
vars (F2). F13-clean: scenarios speak in agent/document language.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.agents.mcp.server import tool_expand
from kairix.use_cases.expand import ExpandDeps
from tests.fakes import FakeDocumentRepository

pytestmark = pytest.mark.bdd

_URI = "sharepoint://site/mcp-doc"
_NINE_WORDS = "alpha beta gamma delta epsilon zeta eta theta iota"


@dataclass
class _McpExpandState:
    chunk_count: int = 0
    envelope: dict[str, Any] = field(default_factory=dict)


@pytest.fixture
def _mcp_expand_state() -> _McpExpandState:
    return _McpExpandState()


def _deps(state: _McpExpandState) -> ExpandDeps:
    documents = [
        {
            "path": f"{_URI}#{seq}",
            "title": "MCP Doc",
            "collection": "team-notes",
            "content": f"{_NINE_WORDS} seq{seq}",
        }
        for seq in range(state.chunk_count)
    ]
    return ExpandDeps(get_chunk=FakeDocumentRepository(documents=documents).get_by_path)


@given(parsers.parse("a document indexed as {count:d} chunks over MCP"))
def _doc_indexed(_mcp_expand_state: _McpExpandState, count: int) -> None:
    _mcp_expand_state.chunk_count = count


@when(parsers.parse("the agent calls the expand tool at chunk {seq:d}"))
def _call_expand(_mcp_expand_state: _McpExpandState, seq: int) -> None:
    _mcp_expand_state.envelope = tool_expand(_URI, seq, token_budget=10_000, deps=_deps(_mcp_expand_state))


@then("the expand tool envelope includes the matched chunk and its neighbours")
def _envelope_has_neighbours(_mcp_expand_state: _McpExpandState) -> None:
    seqs = [c["seq"] for c in _mcp_expand_state.envelope["chunks"]]
    assert {1, 2, 3}.issubset(set(seqs)), f"expected 1,2,3 present; got {seqs!r}"
    matches = [c["seq"] for c in _mcp_expand_state.envelope["chunks"] if c["is_match"]]
    assert matches == [2], f"expected chunk 2 flagged; got {matches!r}"


@then("the expand tool envelope reports no error")
def _envelope_no_error(_mcp_expand_state: _McpExpandState) -> None:
    assert _mcp_expand_state.envelope["error"] == ""


@then("the expand tool envelope says no chunk is stored there")
def _envelope_missing(_mcp_expand_state: _McpExpandState) -> None:
    assert _mcp_expand_state.envelope["chunks"] == []
    assert "no chunk stored" in _mcp_expand_state.envelope["error"]
