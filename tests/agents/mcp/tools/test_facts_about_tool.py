"""Unit tests for ``tool_facts_about`` — the agent-facing fact introspection tool.

Pins the contract the agent sees:

  - Happy path returns the list of hits with the canonical FactRecord
    read-surface fields, sorted by recall score (delegated to FactStore).
  - Empty entity is rejected with the ``InvalidInput`` envelope.
  - Unknown entity returns an empty hits list — not an error.
  - Namespace filtering is honoured (engagement isolation).
  - Superseded facts are filtered out (Protocol default).
  - No-fact_store-injected path resolves a real SQLiteFactStore against
    the supplied KairixPaths (covers the production-wiring branch).
  - Store-search exceptions are caught and surfaced via ``LookupFailed``
    (covers the defensive failure-envelope path).

Every test carries a ``# Sabotage:`` note describing a concrete change
to the production code that would falsify the test.

F1-clean: ``fact_store`` is constructor-injected via the public seam
on tool_facts_about — no monkeypatching.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from kairix.agents.mcp.tools.facts_about import (
    ERROR_INVALID_INPUT,
    ERROR_LOOKUP_FAILED,
    tool_facts_about,
)
from kairix.paths import KairixPaths
from tests.fakes import FakeFactRecord, FakeFactStore

pytestmark = pytest.mark.unit


def _store_with_facts(*records: FakeFactRecord) -> FakeFactStore:
    """Build a FakeFactStore preloaded with the given records."""
    store = FakeFactStore()
    for rec in records:
        store.add(rec)
    return store


def test_happy_path_returns_record_read_surface() -> None:
    """A successful lookup exposes the canonical FactRecord fields.

    Sabotage: drop the ``"confidence"`` key from ``_hit_to_dict`` in
    facts_about.py → the assertion below fails because the field is missing.
    """
    store = _store_with_facts(
        FakeFactRecord(
            id="f-1",
            entity="Alice",
            attribute="role",
            value="founder",
            confidence=0.92,
            source_turn_ids=("t-1", "t-2"),
            extracted_at="2026-05-19T00:00:00Z",
            namespace="engagement-alpha",
        )
    )

    out = tool_facts_about(entity="Alice", fact_store=store)

    assert out["error"] == ""
    assert out["entity"] == "Alice"
    assert len(out["hits"]) == 1
    hit = out["hits"][0]
    assert hit["entity"] == "Alice"
    assert hit["attribute"] == "role"
    assert hit["value"] == "founder"
    assert hit["confidence"] == pytest.approx(0.92)
    assert hit["source_turn_ids"] == ["t-1", "t-2"]
    assert hit["extracted_at"] == "2026-05-19T00:00:00Z"
    assert "score" in hit


def test_empty_entity_is_rejected() -> None:
    """An empty entity string is rejected before any store call.

    Sabotage: remove the ``if not entity:`` guard from tool_facts_about →
    the call reaches ``fact_store.search("")`` and the assertion below
    fails because ``out["error"]`` is "" not "InvalidInput".
    """
    out = tool_facts_about(entity="", fact_store=FakeFactStore())

    assert out["error"] == ERROR_INVALID_INPUT
    assert out["hits"] == []


def test_unknown_entity_returns_empty_hits_not_error() -> None:
    """An entity with no matching records returns ``hits=[]`` and ``error=""``.

    Sabotage: change tool_facts_about to raise ValueError on empty hits →
    the function would now raise instead of returning, breaking the
    "agents read .hits, never raise" contract.
    """
    store = _store_with_facts(FakeFactRecord(id="f-1", entity="Bob", attribute="role", value="engineer"))

    out = tool_facts_about(entity="Charlie", fact_store=store)

    assert out["error"] == ""
    assert out["hits"] == []


def test_namespace_filter_restricts_hits() -> None:
    """Passing ``namespace`` filters out facts in other engagements.

    Sabotage: drop the ``namespace=namespace`` kwarg from
    ``fact_store.search(...)`` inside tool_facts_about → both namespaces
    return facts and the ``len(out["hits"]) == 1`` assertion below fails.
    """
    store = _store_with_facts(
        FakeFactRecord(
            id="f-alpha",
            entity="Alice",
            attribute="role",
            value="founder",
            namespace="engagement-alpha",
        ),
        FakeFactRecord(
            id="f-beta",
            entity="Alice",
            attribute="role",
            value="advisor",
            namespace="engagement-beta",
        ),
    )

    out = tool_facts_about(entity="Alice", namespace="engagement-alpha", fact_store=store)

    assert out["error"] == ""
    assert len(out["hits"]) == 1
    assert out["hits"][0]["value"] == "founder"


def test_superseded_facts_are_filtered_out() -> None:
    """Facts marked superseded are excluded from the agent-facing list.

    Sabotage: change ``FakeFactStore.search`` to include superseded facts
    (drop the ``if fact.superseded_by is not None: continue`` line) — the
    test would now see both records and the assertion below fails.

    This pins the agent-facing contract: the tool returns CURRENT ground
    truth. We use the FakeFactStore's public ``supersede()`` method to
    avoid reaching into internals.
    """
    store = _store_with_facts(
        FakeFactRecord(id="f-old", entity="Alice", attribute="role", value="contractor"),
        FakeFactRecord(id="f-new", entity="Alice", attribute="role", value="founder"),
    )
    store.supersede(old_id="f-old", new_id="f-new")

    out = tool_facts_about(entity="Alice", fact_store=store)

    assert out["error"] == ""
    values = [h["value"] for h in out["hits"]]
    assert "contractor" not in values
    assert "founder" in values


def test_top_k_bounds_result_count() -> None:
    """``top_k`` caps how many hits the tool returns.

    Sabotage: drop the ``top_k=top_k`` kwarg from the
    ``fact_store.search(...)`` call inside tool_facts_about → the default
    of 10 is used and the assertion below fails when top_k=2 is requested.
    """
    store = _store_with_facts(
        *(FakeFactRecord(id=f"f-{i}", entity="Project", attribute="status", value=f"phase-{i}") for i in range(5))
    )

    out = tool_facts_about(entity="Project", top_k=2, fact_store=store)

    assert out["error"] == ""
    assert len(out["hits"]) <= 2


def _paths(tmp_path: Path) -> KairixPaths:
    """Per-test KairixPaths pinned under ``tmp_path`` (hermetic)."""
    return KairixPaths(
        document_root=tmp_path / "vault",
        db_path=tmp_path / "kairix.db",
        log_dir=tmp_path / "logs",
        workspace_root=tmp_path / "workspaces",
    )


def test_no_fact_store_injected_resolves_production_sqlite_store(tmp_path: Path) -> None:
    """When ``fact_store`` is None, the tool builds a SQLiteFactStore.

    Drives the production-wiring branch — when an operator omits
    the DI kwargs, the tool resolves ``KairixPaths`` and constructs a
    real SQLite-backed store. Against a fresh tmp db_path the store has
    no facts, so the lookup returns an empty hits list with no error.

    Sabotage: remove the ``if fact_store is None:`` block in
    ``tool_facts_about`` (lines 106-113) — the call reaches
    ``fact_store.search(...)`` on ``None`` and raises AttributeError,
    failing this test with the unhandled exception. Mutate-confirmed
    against lines 110-113.
    """
    paths = _paths(tmp_path)

    out = tool_facts_about(entity="Alice", paths=paths)

    assert out["error"] == ""
    assert out["entity"] == "Alice"
    assert out["hits"] == []
    # The SQLite db file may not be touched until a write happens (the
    # store defers schema creation to first ``add``); we don't assert on
    # its presence — only that the call returned a clean envelope.


class _RaisingFactStore(FakeFactStore):
    """FakeFactStore subclass whose ``search`` raises RuntimeError.

    Sub-classing the canonical FakeFactStore preserves the full Protocol
    shape without needing per-line coverage pragmas on methods the tool
    doesn't exercise. Only ``search`` is overridden — the inherited
    ``add`` / ``find_conflicts`` / ``supersede`` are never called by
    ``tool_facts_about`` so they incur no coverage cost on this stub.
    """

    def __init__(self, message: str = "simulated store outage") -> None:
        super().__init__()
        self._message = message

    def search(self, query: str, *, top_k: int = 10, namespace: str | None = None) -> list[Any]:
        del query, top_k, namespace
        raise RuntimeError(self._message)


def test_lookup_failure_is_wrapped_in_lookupfailed_envelope() -> None:
    """A RuntimeError out of ``FactStore.search`` is caught and surfaced.

    Drives the except branch — lines 117-126 — which builds the
    canonical ``LookupFailed`` envelope so the agent reads ``error`` and
    branches without seeing a traceback.

    Sabotage: remove the ``try/except`` around the ``fact_store.search``
    call in tool_facts_about → the RuntimeError propagates out of the
    tool, this test fails with the bare RuntimeError instead of the
    LookupFailed assertion. Mutate-confirmed against lines 117-119.
    """
    store = _RaisingFactStore(message="db is missing")

    out = tool_facts_about(entity="Alice", fact_store=store)

    assert out["error"] == ERROR_LOOKUP_FAILED
    assert "db is missing" in out["detail"]
    assert out["hits"] == []
    assert out["entity"] == "Alice"
