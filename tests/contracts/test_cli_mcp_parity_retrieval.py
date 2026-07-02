"""Behavioural CLI ↔ MCP parity for the whole retrieval domain (PLA-322).

W1b split the retrieval MCP adapters thin (they call ``kairix.use_cases.**``);
PLA-322 finished the DRY by folding the residual queue-aware second
implementation onto the search use case. This file is the F43-shaped
behavioural parity proof for the four retrieval capabilities — search /
timeline / expand / entity.

F43 shape: ONE parametrized body run over ≥2 implementations of each
capability — here the two SURFACES (CLI and MCP) are the two impls, driven
from the SAME injected use-case ``deps`` seam and co-asserted in a single
body. That is the parity property: because the fake is injected at the use
case's OWN boundary, BOTH surfaces can only produce its data if BOTH route
through that one use case — and both must serialise the SAME resolvable
``source_uri`` breadcrumb (PLA-274 / F97). A real-only-then-fake-only pair of
bodies could not catch a surface that silently re-serialised a different
breadcrumb; co-asserting the two surfaces in one body does.

Sabotage proof: make the MCP adapter diverge from the use case — e.g. have
``tool_search`` blank the breadcrumb or stop calling ``run_search`` — and the
``cli_row["source_uri"] == mcp_row["source_uri"]`` assertion fires for the
``search`` case. Restored.
"""

from __future__ import annotations

import argparse
import io
import json
from collections.abc import Callable
from contextlib import redirect_stdout
from datetime import date
from types import SimpleNamespace
from typing import Any, NamedTuple

import pytest

from kairix.core.health import HealthDeps
from kairix.core.search.intent import QueryIntent
from tests.fakes import FakeDocumentRepository


# A HealthDeps whose probes are cheap + deterministic so the contract body
# never touches the real filesystem / services (search + entity envelopes
# carry a live health snapshot we do not assert on, only the breadcrumb).
def _cheap_health_deps() -> HealthDeps:
    return HealthDeps(
        secrets_loaded_fn=lambda: True,
        embed_backend_available_fn=lambda: True,
        bm25_index_available_fn=lambda: True,
        neo4j_available_fn=lambda: True,
    )


class RetrievalCapability(NamedTuple):
    """One retrieval capability wired to both surfaces over a shared fake.

    ``mcp_envelope`` / ``cli_envelope`` invoke the two impls (the MCP adapter
    and the CLI adapter) against the SAME injected use-case ``deps``; ``row``
    pulls the breadcrumb-bearing dict out of an envelope so the body compares
    the same shape across surfaces.
    """

    name: str
    expected_source_uri: str
    expected_path: str
    mcp_envelope: Callable[[], dict[str, Any]]
    cli_envelope: Callable[[], dict[str, Any]]
    row: Callable[[dict[str, Any]], dict[str, Any]]


def _capture_json(fn: Callable[[], Any]) -> dict[str, Any]:
    """Run a CLI adapter that prints a JSON envelope; return the parsed dict."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        fn()
    return json.loads(buf.getvalue())


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------
def _search_capability() -> RetrievalCapability:
    from kairix.agents.mcp.server import tool_search
    from kairix.core.search import cli as search_cli
    from kairix.use_cases.search import SearchDeps

    source_uri = "sharepoint://site/q1-report.docx"
    path = "docs/q1-report.md#3"

    inner = SimpleNamespace(
        path=path,
        title="Q1 report",
        snippet="revenue was up",
        boosted_score=0.91,
        collection="agent-alpha",
        source_uri=source_uri,
        seq=3,
        source_page=None,
    )
    budgeted = SimpleNamespace(result=inner, content="revenue was up", tier="vector", token_estimate=9)
    pipeline_result = SimpleNamespace(
        query="q1 revenue",
        intent="semantic",
        results=[budgeted],
        bm25_count=1,
        vec_count=1,
        fused_count=1,
        vec_failed=False,
        total_tokens=9,
        latency_ms=1.0,
        error="",
    )

    def _make_deps() -> SearchDeps:
        return SearchDeps(
            search_fn=lambda **_kwargs: pipeline_result,
            entity_card_fn=lambda _name: None,
            classify_fn=lambda _query: QueryIntent.SEMANTIC,
            health_deps=_cheap_health_deps(),
        )

    def _mcp() -> dict[str, Any]:
        result = tool_search("q1 revenue", deps=_make_deps())
        assert isinstance(result, dict)
        return result

    def _cli() -> dict[str, Any]:
        return _capture_json(lambda: search_cli.main(["q1 revenue", "--json", "--no-entity-card"], deps=_make_deps()))

    return RetrievalCapability(
        name="search",
        expected_source_uri=source_uri,
        expected_path=path,
        mcp_envelope=_mcp,
        cli_envelope=_cli,
        row=lambda env: env["results"][0],
    )


# ---------------------------------------------------------------------------
# timeline
# ---------------------------------------------------------------------------
def _timeline_capability() -> RetrievalCapability:
    from kairix.agents.mcp.server import tool_timeline
    from kairix.core.temporal import cli as temporal_cli
    from kairix.use_cases.timeline import TimelineDeps, run_timeline

    source_uri = "sharepoint://site/board-april.docx"
    path = "boards/april.md"

    chunk = SimpleNamespace(
        text="board approved the April plan",
        date=date(2026, 4, 10),
        metadata={
            "section_heading": "April plan",
            "source_uri": source_uri,
            "collection": "agent-alpha",
            "score": 0.8,
        },
        source_path=path,
        chunk_type="board_card",
    )

    def _make_deps() -> TimelineDeps:
        return TimelineDeps(
            extract_window_fn=lambda _q, _ref: (date(2026, 4, 1), date(2026, 4, 30)),
            rewrite_query_fn=lambda q, _ref: q,
            query_chunks_fn=lambda *_a, **_k: [chunk],
            search_fn=lambda *_a, **_k: SimpleNamespace(results=[]),
        )

    def _mcp() -> dict[str, Any]:
        return tool_timeline("what happened in April", deps=_make_deps())

    def _cli() -> dict[str, Any]:
        deps = _make_deps()
        return _capture_json(
            lambda: temporal_cli.main(
                ["what happened in April", "--json"],
                timeline_runner=lambda *a, **k: run_timeline(*a, **k, deps=deps),
            )
        )

    return RetrievalCapability(
        name="timeline",
        expected_source_uri=source_uri,
        expected_path=path,
        mcp_envelope=_mcp,
        cli_envelope=_cli,
        row=lambda env: env["results"][0],
    )


# ---------------------------------------------------------------------------
# expand
# ---------------------------------------------------------------------------
def _expand_capability() -> RetrievalCapability:
    from kairix.agents.mcp.server import tool_expand
    from kairix.use_cases import expand as expand_uc
    from kairix.use_cases.expand import ExpandDeps

    source_uri = "sharepoint://site/report.docx"
    chunk_path = f"{source_uri}#0"

    def _make_deps() -> ExpandDeps:
        repo = FakeDocumentRepository(
            documents=[
                {
                    "path": chunk_path,
                    "content": "the matched chunk text",
                    "title": "Report",
                    "collection": "agent-alpha",
                }
            ]
        )
        return ExpandDeps(get_chunk=repo.get_by_path, list_chunk_seqs=repo.list_chunk_seqs)

    def _mcp() -> dict[str, Any]:
        return tool_expand(source_uri, 0, deps=_make_deps())

    def _cli() -> dict[str, Any]:
        deps = _make_deps()
        buf = io.StringIO()
        expand_uc.main([source_uri, "0", "--json"], deps=deps, out=buf)
        return json.loads(buf.getvalue())

    return RetrievalCapability(
        name="expand",
        expected_source_uri=source_uri,
        expected_path=chunk_path,
        mcp_envelope=_mcp,
        cli_envelope=_cli,
        row=lambda env: env["chunks"][0],
    )


# ---------------------------------------------------------------------------
# entity
# ---------------------------------------------------------------------------
def _entity_capability() -> RetrievalCapability:
    from kairix.agents.mcp.server import tool_entity
    from kairix.knowledge.entities.cli import cmd_get
    from kairix.use_cases.entity_get import EntityGetDeps

    card = {
        "id": "acme",
        "name": "Acme",
        "type": "Organisation",
        "summary": "consulting client",
        "vault_path": "entities/acme.md",
    }

    def _make_deps() -> EntityGetDeps:
        return EntityGetDeps(fetch_fn=lambda _name: dict(card), health_deps=_cheap_health_deps())

    def _mcp() -> dict[str, Any]:
        return tool_entity("Acme", deps=_make_deps())

    def _cli() -> dict[str, Any]:
        args = argparse.Namespace(name="Acme", format="json")
        return _capture_json(lambda: cmd_get(args, deps=_make_deps()))

    return RetrievalCapability(
        name="entity",
        expected_source_uri="entity://acme",
        expected_path="entities/acme.md",
        mcp_envelope=_mcp,
        cli_envelope=_cli,
        # The entity envelope is flat (no results list); the breadcrumb-bearing
        # "row" is the envelope itself.
        row=lambda env: env,
    )


_CAPABILITIES: list[RetrievalCapability] = [
    _search_capability(),
    _timeline_capability(),
    _expand_capability(),
    _entity_capability(),
]


@pytest.mark.contract
@pytest.mark.parametrize("capability", _CAPABILITIES, ids=lambda c: c.name)
def test_cli_and_mcp_surfaces_return_the_same_breadcrumb(capability: RetrievalCapability) -> None:
    """CLI and MCP surfaces of each retrieval capability agree on the breadcrumb.

    ONE body, run over the two surface impls (``cli`` + ``mcp``) driven from the
    SAME injected use-case ``deps``. Proves (a) both surfaces route through the
    one use case — the injected fake's data only appears if they do — and (b)
    both serialise the identical resolvable ``source_uri`` breadcrumb + display
    ``path`` (PLA-274 / F97), so per-surface pointer drift can't re-accrue.
    """
    envelopes = {"mcp": capability.mcp_envelope(), "cli": capability.cli_envelope()}
    rows = {surface: capability.row(env) for surface, env in envelopes.items()}

    # The MCP surface serialises the injected fake's canonical breadcrumb.
    assert rows["mcp"]["source_uri"] == capability.expected_source_uri
    assert rows["mcp"]["path"] == capability.expected_path

    # Parity: the CLI surface serialises the SAME breadcrumb + display path.
    assert rows["cli"]["source_uri"] == rows["mcp"]["source_uri"], (
        f"{capability.name}: CLI/MCP source_uri breadcrumb drift — "
        f"cli={rows['cli']['source_uri']!r} mcp={rows['mcp']['source_uri']!r}"
    )
    assert rows["cli"]["path"] == rows["mcp"]["path"], (
        f"{capability.name}: CLI/MCP display-path drift — cli={rows['cli']['path']!r} mcp={rows['mcp']['path']!r}"
    )

    # Both surfaces expose the breadcrumb key (same shape) and it is resolvable.
    for surface, row in rows.items():
        assert "source_uri" in row, f"{capability.name}/{surface}: missing source_uri breadcrumb"
        assert row["source_uri"], f"{capability.name}/{surface}: empty (unresolvable) source_uri"
