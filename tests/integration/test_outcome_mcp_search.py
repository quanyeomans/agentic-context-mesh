"""F30 outcome test — MCP ``search`` tool.

``tool_search`` (kairix/agents/mcp/server.py:229) is the thin adapter
around ``kairix.use_cases.search.run_search``. The use case classifies
the query intent, calls a pipeline-shaped ``search_fn``, and projects
each hit into the ``SearchHit`` shape; the MCP tool then runs
``search_output_to_envelope`` to produce the JSON envelope MCP
callers consume.

The F30 contract for MCP tools (``scripts/checks/check_f30_operator_outcome_tests.py``):
call ``tool_<name>`` directly and assert on returned-envelope content
via Subscript/Attribute access — NOT on internal call-counts.

DI seam: existing ``deps`` kwarg on ``tool_search`` (server.py:229-258).
Tests construct ``SearchDeps(search_fn=fake, classify_fn=fake,
entity_card_fn=fake)`` so the envelope is deterministic — no SQLite
FTS, no vector backend, no Neo4j.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from kairix.agents.mcp.server import tool_search
from kairix.core.search.intent import QueryIntent
from kairix.use_cases.search import SearchDeps

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Local lightweight pipeline stand-ins
#
# Mirrors the pattern from tests/use_cases/test_search.py — the run_search
# code path reads ``sr.intent``, ``sr.results``, and per-result
# ``budgeted.result.path/title/boosted_score/collection`` + ``budgeted.content``.
# ---------------------------------------------------------------------------


@dataclass
class _FakeInner:
    path: str = ""
    title: str = ""
    snippet: str = ""
    boosted_score: float = 0.0
    collection: str = ""


@dataclass
class _FakeBudgeted:
    result: _FakeInner
    content: str = ""
    tier: str = ""
    token_estimate: int = 0


@dataclass
class _FakeSearchResult:
    query: str = ""
    intent: Any = QueryIntent.SEMANTIC
    results: list[_FakeBudgeted] = field(default_factory=list)
    bm25_count: int = 0
    vec_count: int = 0
    fused_count: int = 0
    total_tokens: int = 0
    latency_ms: float = 0.0
    vec_failed: bool = False


# ---------------------------------------------------------------------------
# Outcome tests
# ---------------------------------------------------------------------------


def test_tool_search_envelope_carries_results_and_pipeline_counters() -> None:
    """``tool_search`` projects pipeline hits + counters into the envelope.

    Drives the production happy path: one search result with a
    boundary-trimmed content snippet. The envelope must surface every
    load-bearing key — ``results`` (list of hit dicts), ``intent``,
    ``bm25_count`` / ``vec_count`` / ``fused_count``, and ``error``
    empty.

    Sabotage: mutate ``hits = [_budgeted_to_hit(b) for b in ...]`` →
    ``hits = []`` in ``run_search`` → envelope["results"] empty, the
    assertion below on the first result fails. Verified.
    """
    inner = _FakeInner(
        path="docs/note.md",
        title="Note",
        snippet="raw snippet",
        boosted_score=0.85,
        collection="shared",
    )
    budgeted = _FakeBudgeted(
        result=inner,
        content="boundary-trimmed snippet",
        tier="L1",
        token_estimate=42,
    )
    sr = _FakeSearchResult(
        query="topic",
        intent=QueryIntent.SEMANTIC,
        results=[budgeted],
        bm25_count=8,
        vec_count=12,
        fused_count=15,
        total_tokens=42,
        latency_ms=125.5,
    )

    def fake_search(**kwargs: Any) -> _FakeSearchResult:
        return sr

    def fake_classify(query: str) -> QueryIntent:
        return QueryIntent.SEMANTIC

    def fake_card(name: str) -> dict[str, Any] | None:
        return None

    deps = SearchDeps(search_fn=fake_search, classify_fn=fake_classify, entity_card_fn=fake_card)
    envelope = tool_search(query="topic", deps=deps)

    assert isinstance(envelope, dict)
    assert envelope["query"] == "topic", f"query mismatch: {envelope['query']!r}"
    assert envelope["intent"] == "semantic", f"intent mismatch: {envelope['intent']!r}"
    assert envelope["bm25_count"] == 8, f"bm25 count mismatch: {envelope['bm25_count']}"
    assert envelope["vec_count"] == 12, f"vec count mismatch: {envelope['vec_count']}"
    assert envelope["fused_count"] == 15, f"fused count mismatch: {envelope['fused_count']}"
    assert envelope["error"] == "", f"error must be empty on happy path: {envelope['error']!r}"
    assert len(envelope["results"]) == 1, f"expected one hit, got: {envelope['results']!r}"
    hit = envelope["results"][0]
    assert hit["path"] == "docs/note.md", f"hit path mismatch: {hit!r}"
    assert hit["title"] == "Note", f"hit title mismatch: {hit!r}"
    # boundary-trimmed content takes precedence over inner.snippet
    assert hit["snippet"] == "boundary-trimmed snippet", f"hit snippet mismatch: {hit!r}"
    assert hit["score"] == pytest.approx(0.85), f"hit score mismatch: {hit!r}"
    assert hit["tier"] == "L1", f"hit tier mismatch: {hit!r}"


def test_tool_search_envelope_surfaces_pipeline_failure_in_error_field() -> None:
    """A raising ``search_fn`` is caught — envelope returns empty results +
    structured ``error`` string.

    Drives the catch-all branch in ``run_search``. The envelope must
    still return a dict with ``results=[]`` (NOT raise) and ``error``
    non-empty so agents can surface the failure.

    Sabotage: remove the ``except Exception`` block in ``run_search`` so
    the exception propagates. ``tool_search`` doesn't catch — the call
    raises and the envelope-shape assertion below never runs.
    Verified by removing the try/except wrapper.
    """

    def fake_search(**kwargs: Any) -> _FakeSearchResult:
        raise RuntimeError("pipeline down")

    def fake_classify(query: str) -> QueryIntent:
        return QueryIntent.SEMANTIC

    def fake_card(name: str) -> dict[str, Any] | None:
        return None

    deps = SearchDeps(search_fn=fake_search, classify_fn=fake_classify, entity_card_fn=fake_card)
    envelope = tool_search(query="something", deps=deps)

    assert isinstance(envelope, dict)
    assert envelope["query"] == "something"
    assert envelope["results"] == [], f"results must be empty on failure: {envelope['results']!r}"
    assert envelope["error"], f"error must be non-empty on pipeline failure: {envelope!r}"
    assert "RuntimeError" in envelope["error"], f"error must surface exception class: {envelope['error']!r}"
    assert "pipeline down" in envelope["error"], f"error must surface message: {envelope['error']!r}"
