"""F30 outcome test — MCP ``prep`` tool.

``tool_prep`` (kairix/agents/mcp/server.py:297) is the thin adapter
around ``kairix.use_cases.prep.run_prep``. The use case retrieves
candidate documents via ``search_fn``, builds a tier-specific (L0 or
L1) prompt, and asks the LLM (``chat_fn``) to summarise. The MCP tool
projects the ``PrepOutput`` to the JSON envelope.

The F30 contract for MCP tools (``scripts/checks/check_f30_operator_outcome_tests.py``):
call ``tool_<name>`` directly and assert on returned-envelope content
via Subscript/Attribute access — NOT on internal call-counts.

DI seam: existing ``deps`` kwarg on ``tool_prep`` (server.py:297-318).
Tests construct a ``PrepDeps(search_fn=fake, chat_fn=fake)`` so the
envelope is deterministic — no live search backend, no LLM call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from kairix.agents.mcp.server import tool_prep
from kairix.use_cases.prep import PrepDeps

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Local lightweight result stand-ins
#
# The use case reads ``budgeted.result.path/title/snippet`` and
# ``budgeted.content``. Keep them ad-hoc rather than importing real
# pipeline types so the test doesn't drag in unrelated pipeline
# construction surface. (Mirrors the pattern in tests/use_cases/test_prep.py.)
# ---------------------------------------------------------------------------


@dataclass
class _FakeInner:
    snippet: str = ""
    title: str = ""
    path: str = ""


@dataclass
class _FakeBudgeted:
    result: _FakeInner
    content: str = ""


@dataclass
class _FakeSearchResult:
    results: list[_FakeBudgeted] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Outcome tests
# ---------------------------------------------------------------------------

_LONG_SNIPPET = "Alpha is a sample document discussing the topic in detail across paragraphs."


def test_tool_prep_envelope_carries_summary_sources_and_tokens() -> None:
    """``tool_prep`` projects a PrepOutput happy path into the envelope.

    Drives the L0 path: one search result, one canned LLM response. The
    envelope must surface ``summary`` (LLM text), ``sources`` (titles
    or paths used as context), ``tokens`` (positive), ``tier="l0"``,
    and ``error == ""``.

    Sabotage: mutate ``summary = chat(messages=messages,
    max_tokens=max_tokens)`` → ``summary = ""`` in ``run_prep`` →
    envelope["summary"] empty, assertion below fails. Verified.
    """
    sr = _FakeSearchResult(
        results=[_FakeBudgeted(result=_FakeInner(title="doc-alpha", path="/a"), content=_LONG_SNIPPET)]
    )

    def fake_search(**kwargs: Any) -> _FakeSearchResult:
        return sr

    def fake_chat(**kwargs: Any) -> str:
        return "Brief summary of topic alpha."

    deps = PrepDeps(search_fn=fake_search, chat_fn=fake_chat)
    envelope = tool_prep(query="topic alpha", tier="l0", deps=deps)

    assert isinstance(envelope, dict)
    assert envelope["query"] == "topic alpha", f"query mismatch: {envelope['query']!r}"
    assert envelope["tier"] == "l0", f"tier mismatch: {envelope['tier']!r}"
    assert envelope["summary"] == "Brief summary of topic alpha.", f"summary mismatch: {envelope['summary']!r}"
    # PLA-274 / #437 — sources are resolvable SourceRef breadcrumb dicts, not
    # bare title strings. The human title rides on ``title``; the resolvable
    # pointer is ``path`` / ``source_uri`` (source_uri falls back to path here).
    assert [s["title"] for s in envelope["sources"]] == ["doc-alpha"], (
        f"source titles mismatch: {envelope['sources']!r}"
    )
    assert [s["source_uri"] for s in envelope["sources"]] == ["/a"], f"source uris mismatch: {envelope['sources']!r}"
    assert envelope["tokens"] > 0, f"tokens must be positive for non-empty summary: {envelope['tokens']}"
    assert envelope["error"] == "", f"error must be empty on happy path: {envelope['error']!r}"


def test_tool_prep_envelope_no_results_surfaces_no_documents_message() -> None:
    """Empty search results route to the canonical 'No relevant documents' message.

    Drives the no-context branch in ``run_prep`` (lines 274-279). The
    envelope must still surface ``summary`` (the canonical sentinel),
    NOT an empty string — agents key off the substring.

    Sabotage: mutate the ``if not context: return PrepOutput(..., summary="No
    relevant documents...")`` branch to ``summary=""``. The substring
    assertion fails. Verified.
    """

    def fake_search(**kwargs: Any) -> _FakeSearchResult:
        return _FakeSearchResult(results=[])

    def fake_chat(**kwargs: Any) -> str:
        # Should not be called — assertion below covers it indirectly via tokens=0.
        return "should not reach the LLM"

    deps = PrepDeps(search_fn=fake_search, chat_fn=fake_chat)
    envelope = tool_prep(query="obscure topic", deps=deps)

    assert envelope["query"] == "obscure topic"
    assert "No relevant documents" in envelope["summary"], f"missing sentinel summary: {envelope['summary']!r}"
    assert envelope["sources"] == [], f"sources must be empty when no results: {envelope['sources']!r}"
    assert envelope["error"] == "", f"error must be empty for no-results branch: {envelope['error']!r}"
