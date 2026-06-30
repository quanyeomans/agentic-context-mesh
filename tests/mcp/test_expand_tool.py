"""F30 outcome test for the MCP ``tool_expand`` handler (PLA-268).

Direct handler call with an ``ExpandDeps`` injected (the canonical
``FakeDocumentRepository.get_by_path`` chunk reader), asserting on the
returned envelope — no SQLite, no monkeypatching.
"""

from __future__ import annotations

import pytest

from kairix.agents.mcp.server import tool_expand
from kairix.use_cases.expand import ExpandDeps
from tests.fakes import FakeDocumentRepository

pytestmark = pytest.mark.unit

_URI = "sharepoint://site/doc-beta"
_NINE_WORDS = "alpha beta gamma delta epsilon zeta eta theta iota"


def _deps(count: int) -> ExpandDeps:
    documents = [
        {
            "path": f"{_URI}#{seq}",
            "title": "Beta Doc",
            "collection": "team-notes",
            "content": f"{_NINE_WORDS} seq{seq}",
        }
        for seq in range(count)
    ]
    return ExpandDeps(get_chunk=FakeDocumentRepository(documents=documents).get_by_path)


def test_tool_expand_returns_neighbour_window_envelope() -> None:
    envelope = tool_expand(_URI, 1, token_budget=10_000, deps=_deps(3))

    assert envelope["error"] == ""
    assert envelope["source_uri"] == _URI
    assert envelope["matched_seq"] == 1
    seqs = [c["seq"] for c in envelope["chunks"]]
    assert seqs == [0, 1, 2]
    # Each row embeds the resolvable SourceRef breadcrumb (PLA-274 / F97).
    assert envelope["chunks"][1]["source_ref"]["source_uri"] == _URI
    assert envelope["chunks"][1]["is_match"] is True


def test_tool_expand_reports_missing_chunk_in_envelope() -> None:
    envelope = tool_expand(_URI, 42, deps=_deps(3))

    assert envelope["chunks"] == []
    assert "no chunk stored" in envelope["error"]
