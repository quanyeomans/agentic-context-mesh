"""Unit tests for ``tool_facts_about`` — the agent-facing fact introspection tool.

Pins the contract the agent sees:

  - Happy path returns the list of hits with the canonical FactRecord
    read-surface fields, sorted by recall score (delegated to FactStore).
  - Empty entity is rejected with the ``InvalidInput`` envelope.
  - Unknown entity returns an empty hits list — not an error.
  - Namespace filtering is honoured (engagement isolation).
  - Superseded facts are filtered out (Protocol default).

Every test carries a ``# Sabotage:`` note describing a concrete change
to the production code that would falsify the test.

F1-clean: ``fact_store`` is constructor-injected via the public seam
on tool_facts_about — no monkeypatching.
"""

from __future__ import annotations

import pytest

from kairix.agents.mcp.tools.facts_about import (
    ERROR_INVALID_INPUT,
    tool_facts_about,
)
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
