"""Integration test for the MCP cold-start affordance (#278).

When an agent calls an MCP tool on a not-yet-warmed kairix, the gated
tools return the structured ColdStart envelope immediately — never a
silent multi-second block, never an opaque "fetch failed". The envelope
carries the retry ETA and guidance so the agent commits 'kairix is
warming up, retry in N seconds' to memory rather than 'kairix is flaky'.

Diagnostic tools (usage_guide, onboard_check, worker_status, warm,
capabilities, probes, operator escalations) are deliberately NOT gated
— they exist to diagnose the cold state itself or to perform the
warm-up.

Gating shape: ``@warm_gate`` decorator in ``kairix/agents/mcp/server.py``.
The decorator is the single source of truth — these tests pin its
behaviour across every gated tool via parametrisation, so adding /
removing a gated tool only requires updating the table below, not
writing another test function.

The per-directory conftest pre-marks state warm via an autouse fixture,
so EVERY OTHER integration test runs the production tool path. This
module overrides that fixture to reset cold so the affordance path fires.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from kairix.agents.mcp.server import build_server
from kairix.agents.mcp.tools.facts_about import FactsAboutDeps
from kairix.platform.warm.state import reset_warm_state
from kairix.use_cases.remember import RememberDeps
from tests.fakes import FakeDocumentRepository, FakeFactRecord, FakeFactStore

pytestmark = pytest.mark.integration

# Single-source the literals the cold-write tests reuse so the module stays
# free of duplicate string literals (Sonar S1192 / F17).
_MEMORY_WRITE = "memory_write"
_ALPHA = "agent-alpha"
_ALPHA_SURFACE = "04-Agent-Knowledge/agent-alpha"
_COLD_NOW = datetime(2026, 6, 30, 9, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Tool inventories — single source of truth for which tools gate and which
# don't. Adding a new tool means updating ONE entry; the parametrised tests
# below cover it automatically.
# ---------------------------------------------------------------------------

# (tool_name, minimal_sample_args) for every ``@warm_gate``-decorated tool
# in ``build_server``. Order matches the ``build_server`` registration order
# so the file reads top-to-bottom alongside ``server.py``.
GATED_TOOLS: list[tuple[str, dict[str, Any]]] = [
    ("search", {"query": "anything", "budget": 1000}),
    ("entity", {"name": "anything"}),
    ("prep", {"query": "anything"}),
    ("timeline", {"query": "anything"}),
    ("research", {"query": "anything"}),
    ("contradict", {"content": "anything"}),
    ("brief", {"agent": "anyone"}),
    ("bootstrap", {"agent": "anyone"}),
    ("entity_suggest", {"text": "anything"}),
    ("entity_validate", {"name": "anything"}),
    # Plan B-parity Week 5 Stream A — agent-driven ingest + recall.
    # ingest_chat stays gated (it drives the LLM fact extractor); facts_about
    # moved to UNGATED_TOOLS below (PLA-263 — both its legs are cheap local
    # SQLite reads, so it must answer while cold).
    (
        "ingest_chat",
        {"jsonl_content": "{}\n", "conversation_id": "anything", "namespace": "anything"},
    ),
]

# Tools that must STILL serve real responses while cold — they exist to
# diagnose the cold state itself, return static content, OR (memory_write)
# perform a write that doesn't depend on warmth and must never be refused.
UNGATED_TOOLS: list[tuple[str, dict[str, Any]]] = [
    ("usage_guide", {}),
    ("onboard_check", {}),
    # PLA-257 — memory_write is NOT warm-gated: an agent records a decision
    # at session start, when the embedding model is still warming, and the
    # write doesn't depend on warmth. The empty-content guard fires before
    # any config / filesystem resolution, so this stays hermetic — the point
    # is only that the body RAN (returned its own validation error) rather
    # than the ColdStart short-circuit.
    (_MEMORY_WRITE, {"agent": _ALPHA, "content": ""}),
    # PLA-263 — facts_about is NOT warm-gated: it reads only the SQLite fact
    # store + the entity-summaries collection (no embedding model, no network),
    # so an agent asking "what do you know about X?" at session start gets an
    # answer. With no injected deps the body resolves the real (empty/fresh)
    # SQLite index and returns error="" (or a LookupFailed envelope on a db
    # fault) — never the ColdStart short-circuit. The dedicated test below
    # proves a real fact + summary are SERVED while cold via injected fakes.
    ("facts_about", {"entity": "anything"}),
]


# ---------------------------------------------------------------------------
# Test harness
# ---------------------------------------------------------------------------


def _payload_from_call(raw: Any) -> dict[str, Any]:
    """Decode whatever shape FastMCP's call_tool returned into a dict."""
    if isinstance(raw, tuple) and len(raw) == 2 and isinstance(raw[1], dict):
        return raw[1]
    if isinstance(raw, list) and raw and hasattr(raw[0], "text"):
        return json.loads(raw[0].text)
    return raw  # type: ignore[no-any-return]  # raw shape varies across FastMCP versions; runtime narrowing


def _call_tool(server: Any, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return _payload_from_call(asyncio.run(server.call_tool(name, arguments)))


@pytest.fixture(autouse=True)
def _force_cold_state() -> None:
    """Override the directory-level autouse fixture — these tests want cold."""
    reset_warm_state()
    yield
    reset_warm_state()


# ---------------------------------------------------------------------------
# Cold-start envelope on every gated tool
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("tool_name", "sample_args"), GATED_TOOLS)
def test_gated_tool_returns_cold_start_envelope_when_cold(
    tool_name: str,
    sample_args: dict[str, Any],
) -> None:
    """Every ``@warm_gate``-decorated MCP tool returns the ColdStart envelope
    on a not-yet-warm container — instead of "fetch failed", instead of an
    8-second block, instead of an opaque error.

    Sabotage-proof: remove ``@warm_gate`` from any tool in ``build_server``
    and this parametrised case fires for that tool — the body runs against
    the not-yet-warm pipeline and the envelope assertion fails.

    Envelope shape is pinned: ``error == "ColdStart"``, ``tool`` matches
    the calling tool name, ``guidance`` carries a retry ETA, and the
    ``estimated_seconds_remaining`` field gives the agent a number to wait.
    """
    server = build_server(host="127.0.0.1", port=18099)

    payload = _call_tool(server, tool_name, sample_args)

    assert payload.get("error") == "ColdStart", (
        f"cold {tool_name} must return ColdStart envelope; "
        f"got error={payload.get('error')!r}, keys={sorted(payload.keys())}"
    )
    assert payload["tool"] == tool_name
    assert "Retry" in payload["guidance"]
    assert "estimated_seconds_remaining" in payload


# ---------------------------------------------------------------------------
# Diagnostic / static tools must NOT gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("tool_name", "sample_args"), UNGATED_TOOLS)
def test_ungated_tool_serves_while_cold(
    tool_name: str,
    sample_args: dict[str, Any],
) -> None:
    """Diagnostic and static tools must serve real responses while kairix
    is cold — operators need them to diagnose what's wrong, and ``usage_guide``
    returns a static document agents can read while waiting for warm-up.

    Sabotage-proof: add ``@warm_gate`` to one of these tools in
    ``build_server`` and this parametrised case fires — the tool starts
    returning ColdStart instead of its real response.
    """
    server = build_server(host="127.0.0.1", port=18099)

    payload = _call_tool(server, tool_name, sample_args)

    assert payload.get("error") != "ColdStart", (
        f"{tool_name} must serve while cold; got ColdStart envelope: {payload!r}"
    )


# ---------------------------------------------------------------------------
# memory_write persists on a cold container (PLA-257)
# ---------------------------------------------------------------------------
#
# The parametrised UNGATED case above proves memory_write doesn't return the
# ColdStart envelope. These tests go one step further: they drive a real
# write through the live MCP dispatch surface while cold and assert the file
# lands on disk — the behaviour the bug took away. ``remember_deps`` is the
# build_server injection seam (F1/F2-clean — a constructor seam, no
# monkeypatch, no env vars) so the write targets a tmp knowledge store, never
# the live tree.


def _cold_remember_deps(tmp_path: Path, *, indexed: bool) -> RememberDeps:
    """RememberDeps over tmp paths for the cold-write tests.

    ``index_fn`` is pinned so a test can model both outcomes: indexing
    completing (searchable now) and indexing being unavailable (the memory
    is saved and queued for the next indexing pass).
    """
    return RememberDeps(
        config_fn=lambda: {
            "agents": {
                _ALPHA: {
                    "harness": "claude-code",
                    "surfaces": [{"path": _ALPHA_SURFACE, "label": "memory"}],
                }
            }
        },
        document_root_fn=lambda: tmp_path / "vault",
        db_path_fn=lambda: tmp_path / "index.sqlite",
        now_fn=lambda: _COLD_NOW,
        index_fn=lambda _db, _root, _hash: indexed,
    )


def test_memory_write_persists_file_while_cold(tmp_path: Path) -> None:
    """A memory_write on a NOT-yet-warm kairix persists the file through the
    live MCP dispatch path instead of refusing with the ColdStart envelope.

    This is the PLA-257 bug: an agent records a decision at session start —
    while the embedding model is still warming — and the warm gate rejected
    the write. The write doesn't depend on warmth, so it must land.

    Sabotage-proof: re-add ``@warm_gate`` above ``memory_write`` in
    ``build_server``; the cold gate short-circuits before the body runs, so
    ``error`` becomes ``"ColdStart"``, no ``path`` is returned, and both the
    ``error == ""`` and ``Path(...).exists()`` assertions fail.
    """
    reset_warm_state()  # genuine cold — a present gate would fire here
    server = build_server(
        host="127.0.0.1",
        port=18099,
        remember_deps=_cold_remember_deps(tmp_path, indexed=True),
    )

    payload = _call_tool(
        server,
        _MEMORY_WRITE,
        {"agent": _ALPHA, "content": "decided: ship the warm-gate fix", "kind": "decision"},
    )

    assert payload.get("error") == "", f"cold memory_write must persist, not refuse; got {payload!r}"
    assert payload["indexed"] is True
    written = Path(payload["path"])
    assert written.exists(), f"expected the memory file persisted at {written}"
    assert written.parent == tmp_path / "vault" / _ALPHA_SURFACE
    assert "ship the warm-gate fix" in written.read_text(encoding="utf-8")


def test_memory_write_cold_returns_queued_status_when_indexing_unavailable(tmp_path: Path) -> None:
    """When immediate indexing can't complete on a cold kairix, the write
    still persists and the envelope reports a "saved, queued for indexing"
    status (indexed=False + the re-index affordance) rather than rejecting.

    Sabotage-proof: re-add ``@warm_gate`` above ``memory_write``; the gate
    returns the ColdStart envelope before the body runs, so ``error`` becomes
    ``"ColdStart"``, ``indexed`` and ``path`` are absent, and the queued-status
    + file-exists assertions fail.
    """
    reset_warm_state()
    server = build_server(
        host="127.0.0.1",
        port=18099,
        remember_deps=_cold_remember_deps(tmp_path, indexed=False),
    )

    payload = _call_tool(
        server,
        _MEMORY_WRITE,
        {"agent": _ALPHA, "content": "fact: the usearch view maps lazily on first query"},
    )

    assert payload.get("error") == "", f"cold memory_write must persist even when un-indexed; got {payload!r}"
    assert payload["indexed"] is False
    assert "next: run kairix embed" in payload["detail"]
    assert Path(payload["path"]).exists()


# ---------------------------------------------------------------------------
# facts_about serves real answers on a cold container (PLA-263)
# ---------------------------------------------------------------------------
#
# The parametrised UNGATED case above proves facts_about doesn't return the
# ColdStart envelope. This goes one step further: it drives a real lookup
# through the live MCP dispatch surface while cold and asserts BOTH a fact and
# an entity summary come back — the answer the warm gate used to refuse.
# ``facts_about_deps`` is the build_server injection seam (F1/F2-clean — a
# constructor seam, no monkeypatch, no env vars) so the read targets scripted
# fakes, never the live tree.


def test_facts_about_serves_fact_and_summary_while_cold() -> None:
    """A facts_about call on a NOT-yet-warm kairix returns the entity's fact
    AND its indexed entity summary, through the live MCP dispatch path,
    instead of refusing with the ColdStart envelope.

    Sabotage-proof: re-add ``@warm_gate`` above ``facts_about`` in
    ``build_server``; the cold gate short-circuits before the body runs, so
    ``error`` becomes ``"ColdStart"``, no ``hits`` / ``entity_summaries`` are
    returned, and every assertion below fails. Mutate-confirmed.
    """
    reset_warm_state()  # genuine cold — a present gate would fire here
    fact_store = FakeFactStore()
    fact_store.add(FakeFactRecord(id="f-1", entity="Acme Corp", attribute="industry", value="manufacturing"))
    document_repo = FakeDocumentRepository(
        documents=[
            {
                "path": "entity://Q-acme#0",
                "collection": "entity-summaries",
                "title": "",
                "content": "Acme Corp is a fictional manufacturing company.",
            }
        ]
    )
    server = build_server(
        host="127.0.0.1",
        port=18099,
        facts_about_deps=FactsAboutDeps(fact_store=fact_store, document_repo=document_repo, canonicals=[]),
    )

    payload = _call_tool(server, "facts_about", {"entity": "Acme Corp"})

    assert payload.get("error") == "", f"cold facts_about must serve, not refuse; got {payload!r}"
    values = [h["value"] for h in payload["hits"]]
    assert "manufacturing" in values
    summaries = [s["summary"] for s in payload["entity_summaries"]]
    assert any("manufacturing company" in s for s in summaries), (
        f"expected the entity summary served while cold; got {summaries!r}"
    )
