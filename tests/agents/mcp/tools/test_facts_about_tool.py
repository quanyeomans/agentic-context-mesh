"""Unit tests for ``tool_facts_about`` — the agent-facing fact introspection tool.

Pins the contract the agent sees:

  - Happy path returns the list of hits with the canonical FactRecord
    read-surface fields, sorted by recall score (delegated to FactStore).
  - Empty entity is rejected with the ``InvalidInput`` envelope.
  - Unknown entity returns an empty hits list — not an error.
  - Namespace filtering is honoured (engagement isolation).
  - Superseded facts are filtered out (Protocol default).
  - The synthetic ``entity-summaries`` collection (#467 / PLA-263) is
    queried so an indexed entity summary surfaces even with no facts.
  - No-store-injected path resolves a real SQLiteFactStore +
    SQLiteDocumentRepository against the supplied KairixPaths (covers
    the production-wiring branch).
  - Store-search exceptions are caught and surfaced via ``LookupFailed``
    (covers the defensive failure-envelope path).

Every test carries a ``# Sabotage:`` note describing a concrete change
to the production code that would falsify the test.

F1-clean: ``fact_store`` / ``document_repo`` are constructor-injected via
the public seam on tool_facts_about — no monkeypatching.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from kairix.agents.mcp.tools.facts_about import (
    ENTITY_SUMMARIES_COLLECTION,
    ERROR_INVALID_INPUT,
    ERROR_LOOKUP_FAILED,
    tool_facts_about,
)
from kairix.paths import KairixPaths
from tests.fakes import FakeDocumentRepository, FakeFactRecord, FakeFactStore

pytestmark = pytest.mark.unit


def _store_with_facts(*records: FakeFactRecord) -> FakeFactStore:
    """Build a FakeFactStore preloaded with the given records."""
    store = FakeFactStore()
    for rec in records:
        store.add(rec)
    return store


def _empty_doc_repo() -> FakeDocumentRepository:
    """An empty document store so the entity-summary leg returns ``[]``.

    Injected on the fact-only tests below so the call never reaches the
    real SQLite document store (hermetic) — the tests that exercise the
    entity-summary leg pass their own pre-loaded repo.
    """
    return FakeDocumentRepository()


def _doc_repo_with_summary(*, path: str, summary: str) -> FakeDocumentRepository:
    """A document store holding one ``entity-summaries`` chunk."""
    return FakeDocumentRepository(
        documents=[{"path": path, "collection": ENTITY_SUMMARIES_COLLECTION, "title": "", "content": summary}]
    )


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

    out = tool_facts_about(entity="Alice", fact_store=store, document_repo=_empty_doc_repo())

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


def test_read_surface_exposes_evidence_at_id_and_namespace() -> None:
    """The read surface carries ``evidence_at``, ``id`` and ``namespace``.

    Temporal recall ("when did X move?") depends on ``evidence_at`` — the
    real event-time the fact occurred — NOT ``extracted_at`` (wall-clock
    at extraction). This test uses a record whose ``evidence_at`` differs
    from ``extracted_at`` so it pins that the tool surfaces the event-time,
    the deterministic ``id`` (for dedup/citation), and the ``namespace``
    (engagement scope) — all of which the old ``_hit_to_dict`` dropped.

    Sabotage: drop any one of the ``"id"``, ``"evidence_at"`` or
    ``"namespace"`` keys from ``_hit_to_dict`` in facts_about.py → the
    matching assertion below fails because the field is missing. Confirmed
    by mutating each key out in turn.
    """
    store = _store_with_facts(
        FakeFactRecord(
            id="f-evt-1",
            entity="Acme HQ",
            attribute="location",
            value="Berlin",
            confidence=0.88,
            source_turn_ids=("t-9",),
            extracted_at="2026-06-30T12:00:00Z",
            evidence_at="2025-11-02T00:00:00Z",
            namespace="engagement-gamma",
        )
    )

    out = tool_facts_about(entity="Acme HQ", fact_store=store, document_repo=_empty_doc_repo())

    assert out["error"] == ""
    assert len(out["hits"]) == 1
    hit = out["hits"][0]
    assert hit["id"] == "f-evt-1"
    assert hit["namespace"] == "engagement-gamma"
    # evidence_at is the event-time, distinct from the extraction wall-clock.
    assert hit["evidence_at"] == "2025-11-02T00:00:00Z"
    assert hit["extracted_at"] == "2026-06-30T12:00:00Z"
    assert hit["evidence_at"] != hit["extracted_at"]


def test_empty_entity_is_rejected() -> None:
    """An empty entity string is rejected before any store call.

    Sabotage: remove the ``if not entity:`` guard from tool_facts_about →
    the call reaches ``fact_store.search("")`` and the assertion below
    fails because ``out["error"]`` is "" not "InvalidInput".
    """
    out = tool_facts_about(entity="", fact_store=FakeFactStore(), document_repo=_empty_doc_repo())

    assert out["error"] == ERROR_INVALID_INPUT
    assert out["hits"] == []
    assert out["entity_summaries"] == []


def test_unknown_entity_returns_empty_hits_not_error() -> None:
    """An entity with no matching records returns ``hits=[]`` and ``error=""``.

    Sabotage: change tool_facts_about to raise ValueError on empty hits →
    the function would now raise instead of returning, breaking the
    "agents read .hits, never raise" contract.
    """
    store = _store_with_facts(FakeFactRecord(id="f-1", entity="Bob", attribute="role", value="engineer"))

    out = tool_facts_about(entity="Charlie", fact_store=store, document_repo=_empty_doc_repo())

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

    out = tool_facts_about(
        entity="Alice", namespace="engagement-alpha", fact_store=store, document_repo=_empty_doc_repo()
    )

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

    out = tool_facts_about(entity="Alice", fact_store=store, document_repo=_empty_doc_repo())

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

    out = tool_facts_about(entity="Project", top_k=2, fact_store=store, document_repo=_empty_doc_repo())

    assert out["error"] == ""
    assert len(out["hits"]) <= 2


# ---------------------------------------------------------------------------
# PLA-261 — actionable source breadcrumb on the read surface
# ---------------------------------------------------------------------------


def test_hit_carries_resolvable_source_uri_from_conversation() -> None:
    """A fact grounded in a conversation surfaces a re-openable ``source_uri``
    plus the shared ``source_ref`` breadcrumb — not just opaque turn-ids.

    This is the headline PLA-261 contract: an agent can open the source to
    verify/act on a recalled fact (the recall→verify→act loop #467 broke).

    Sabotage-proof (executed): replace ``resolve_fact_source_uri(record)``
    with ``""`` in ``FactView.from_hit`` → ``source_uri`` is empty and both
    the source_uri and source_ref assertions below fail.
    """
    store = _store_with_facts(
        FakeFactRecord(
            id="f-conv",
            entity="Alice",
            attribute="role",
            value="founder",
            source_turn_ids=("t-1",),
            conversation_id="session-001",
        )
    )

    out = tool_facts_about(entity="Alice", fact_store=store, document_repo=_empty_doc_repo())

    hit = out["hits"][0]
    assert hit["conversation_id"] == "session-001"
    assert hit["source_uri"] == "04-Agent-Knowledge/conversations/session-001.md"
    # The shared SourceRef breadcrumb (F97) — same resolvable pointer.
    assert hit["source_ref"]["source_uri"] == "04-Agent-Knowledge/conversations/session-001.md"
    # Opaque turn-ids are still present (provenance), now joined by a pointer.
    assert hit["source_turn_ids"] == ["t-1"]


def test_hit_source_uri_prefers_stored_federated_provenance() -> None:
    """A fact carrying real connector provenance (#429) surfaces that URI,
    not the conversation fallback — federated provenance travels through.

    Sabotage-proof (executed): in ``resolve_fact_source_uri`` drop the
    ``if explicit:`` early return → the stored ``m365://`` URI is discarded
    and this assertion fails.
    """
    store = _store_with_facts(
        FakeFactRecord(
            id="f-fed",
            entity="Acme",
            attribute="industry",
            value="widgets",
            conversation_id="session-009",
            source_uri="m365://sites/acme/doc-42",
        )
    )

    out = tool_facts_about(entity="Acme", fact_store=store, document_repo=_empty_doc_repo())

    assert out["hits"][0]["source_uri"] == "m365://sites/acme/doc-42"


def test_legacy_hit_without_provenance_still_carries_a_source_uri() -> None:
    """A pre-breadcrumb fact (no conversation_id / source_uri) still gets a
    non-empty ``source_uri`` — the ``facts://<id>`` self-pointer — so the
    SLO "100% of memory-read results carry a source_uri" holds.

    Sabotage-proof (executed): change the final fallback in
    ``resolve_fact_source_uri`` to ``return ""`` → ``source_uri`` is empty
    and the truthiness assertion below fails.
    """
    store = _store_with_facts(FakeFactRecord(id="f-legacy", entity="Bob", attribute="status", value="active"))

    out = tool_facts_about(entity="Bob", fact_store=store, document_repo=_empty_doc_repo())

    hit = out["hits"][0]
    assert hit["source_uri"], "every hit must carry a non-empty resolvable source_uri"
    assert hit["source_uri"] == "facts://f-legacy"
    assert hit["conversation_id"] is None


# ---------------------------------------------------------------------------
# #467 / PLA-263 — the entity-summaries collection is queried
# ---------------------------------------------------------------------------


def test_entity_summary_is_surfaced_from_the_collection() -> None:
    """An entity with a summary chunk but NO extracted facts still gets an
    answer — the entity summary comes back in ``entity_summaries``.

    This is the headline PLA-263 bug: ``facts_about`` ignored the
    ``entity-summaries`` collection that ``entity_summary_indexing_enabled``
    populates, so a query about an entity that only had a Wikidata-style
    summary returned nothing.

    Sabotage-proof: delete the ``entity_summaries = _query_entity_summaries(...)``
    line in tool_facts_about (or stop passing the result into the response)
    → ``out["entity_summaries"]`` is empty and the assertions below fail.
    Mutate-confirmed by replacing the call with ``entity_summaries = []``.
    """
    summary = "Acme Corp is a fictional manufacturing company."
    repo = _doc_repo_with_summary(path="entity://Q-acme#0", summary=summary)

    out = tool_facts_about(entity="Acme Corp", fact_store=FakeFactStore(), document_repo=repo)

    assert out["error"] == ""
    assert out["hits"] == []  # no facts — the summary is the only signal
    assert len(out["entity_summaries"]) == 1
    surfaced = out["entity_summaries"][0]
    assert surfaced["summary"] == summary
    assert surfaced["source"] == "entity://Q-acme#0"
    assert "score" in surfaced


def test_entity_summary_query_is_scoped_to_the_entity_summaries_collection() -> None:
    """The summary leg searches ONLY the ``entity-summaries`` collection.

    Sabotage-proof: change the ``collections=[ENTITY_SUMMARIES_COLLECTION]``
    arg in ``_query_entity_summaries`` to ``None`` (search-all) → the
    recorded call no longer carries the scoped collection list and the
    assertion below fails. Mutate-confirmed.
    """
    repo = _doc_repo_with_summary(path="entity://Q-acme#0", summary="Acme Corp makes widgets.")

    tool_facts_about(entity="Acme Corp", top_k=7, fact_store=FakeFactStore(), document_repo=repo)

    assert repo.calls == [("Acme Corp", [ENTITY_SUMMARIES_COLLECTION], 7)]


def test_entity_summary_projection_reads_production_bm25_row_fields() -> None:
    """A production-shaped BM25 row (``snippet`` / ``file`` / ``score`` present,
    NO ``content`` / ``path``) projects each field from its primary key.

    Sabotage-proof: flip the FIRST ``or`` in any of the three
    ``_entity_summary_to_dict`` fallback chains to ``and`` (e.g.
    ``row.get("snippet") or row.get("content")`` → ``... and ...``) → the
    projected field collapses to the absent fallback operand
    (``str(None)`` == "None", or score 0.0) and the exact-value assertion
    below fails. Mutate-confirmed for the snippet, file, and score chains.
    """
    repo = FakeDocumentRepository(
        bm25_rows=[
            {
                "file": "entity://Q-acme#0",
                "snippet": "Acme Corp is a manufacturing company.",
                "score": 0.42,
                "collection": ENTITY_SUMMARIES_COLLECTION,
            }
        ]
    )

    out = tool_facts_about(entity="Acme Corp", fact_store=FakeFactStore(), document_repo=repo)

    assert len(out["entity_summaries"]) == 1
    surfaced = out["entity_summaries"][0]
    assert surfaced["summary"] == "Acme Corp is a manufacturing company."
    assert surfaced["source"] == "entity://Q-acme#0"
    assert surfaced["score"] == pytest.approx(0.42)


def test_entity_summary_projection_falls_back_to_fake_row_fields() -> None:
    """A fake-shaped row (``content`` / ``path`` present, NO ``snippet`` /
    ``file``) projects via the SECOND operand of each fallback chain.

    Sabotage-proof: flip the SECOND ``or`` in the snippet/file chains to
    ``and`` (e.g. ``row.get("content") or ""`` → ``row.get("content") and ""``)
    → the projection collapses to ``""`` and the exact-value assertions
    below fail. Pairs with the production-row test above so BOTH ``or``
    operators on each chain are pinned. Mutate-confirmed.
    """
    repo = FakeDocumentRepository(
        bm25_rows=[
            {
                "content": "Globex builds gadgets.",
                "path": "entity://Q-globex#0",
                "collection": ENTITY_SUMMARIES_COLLECTION,
            }
        ]
    )

    out = tool_facts_about(entity="Globex", fact_store=FakeFactStore(), document_repo=repo)

    assert len(out["entity_summaries"]) == 1
    surfaced = out["entity_summaries"][0]
    assert surfaced["summary"] == "Globex builds gadgets."
    assert surfaced["source"] == "entity://Q-globex#0"


def test_entity_summaries_empty_when_no_summary_matches() -> None:
    """No matching summary chunk → ``entity_summaries == []`` with no error.

    Sabotage: make ``_entity_summary_to_dict`` fabricate a row on empty
    input → the empty-collection case would report a spurious summary and
    this assertion fails.
    """
    repo = _doc_repo_with_summary(path="entity://Q-other#0", summary="Globex builds gadgets.")

    out = tool_facts_about(entity="Acme Corp", fact_store=FakeFactStore(), document_repo=repo)

    assert out["error"] == ""
    assert out["entity_summaries"] == []


def test_entity_summary_lookup_failure_is_isolated_from_facts() -> None:
    """A document-store outage degrades to facts-only — it never sinks the call.

    Sabotage: remove the ``try/except`` around ``document_repo.search_fts``
    in ``_query_entity_summaries`` → the raised error propagates out of the
    tool and this test fails with the bare exception instead of the
    facts-only envelope. Mutate-confirmed.
    """
    store = _store_with_facts(FakeFactRecord(id="f-1", entity="Acme Corp", attribute="industry", value="widgets"))
    failing_repo = FakeDocumentRepository(raises=RuntimeError("document store offline"))

    out = tool_facts_about(entity="Acme Corp", fact_store=store, document_repo=failing_repo)

    assert out["error"] == ""
    assert out["entity_summaries"] == []
    assert [h["value"] for h in out["hits"]] == ["widgets"]


def _paths(tmp_path: Path) -> KairixPaths:
    """Per-test KairixPaths pinned under ``tmp_path`` (hermetic)."""
    return KairixPaths(
        document_root=tmp_path / "vault",
        db_path=tmp_path / "kairix.db",
        log_dir=tmp_path / "logs",
        workspace_root=tmp_path / "workspaces",
    )


def test_no_store_injected_resolves_production_sqlite_stores(tmp_path: Path) -> None:
    """When ``fact_store`` AND ``document_repo`` are None, the tool builds
    a real ``SQLiteFactStore`` + ``SQLiteDocumentRepository``.

    Drives the production-wiring branch — when an operator omits the DI
    kwargs, the tool resolves ``KairixPaths`` and constructs both real
    SQLite-backed seams. Against a fresh tmp db_path both stores are empty,
    so the lookup returns empty ``hits`` and ``entity_summaries`` with no
    error.

    Sabotage: remove the ``if fact_store is None:`` (or ``if document_repo
    is None:``) block in ``tool_facts_about`` — the corresponding seam stays
    ``None`` and the call raises AttributeError when it is used, failing this
    test with the unhandled exception. Mutate-confirmed.
    """
    paths = _paths(tmp_path)

    out = tool_facts_about(entity="Alice", paths=paths)

    assert out["error"] == ""
    assert out["entity"] == "Alice"
    assert out["hits"] == []
    assert out["entity_summaries"] == []


def test_missing_fact_store_is_resolved_when_only_document_repo_injected(tmp_path: Path) -> None:
    """When ONLY ``document_repo`` is injected, the tool still resolves the
    fact store from ``paths`` — the resolution guard fires if EITHER seam is
    missing, not only when BOTH are.

    Sabotage-proof: change the ``or`` to ``and`` in the resolution guard
    (``if fact_store is None or document_repo is None``). With ``document_repo``
    injected the guard becomes ``True and False`` → False → the block is
    skipped, ``fact_store`` stays ``None``, and ``fact_store.search(...)``
    raises ``AttributeError`` — which is NOT in the caught
    ``(OSError, ValueError, RuntimeError)`` tuple — so it propagates and this
    test errors. Mutate-confirmed (``or`` → ``and`` at the guard).
    """
    paths = _paths(tmp_path)

    out = tool_facts_about(entity="Alice", paths=paths, document_repo=_empty_doc_repo())

    assert out["error"] == ""
    assert out["hits"] == []


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

    Drives the except branch which builds the canonical ``LookupFailed``
    envelope so the agent reads ``error`` and branches without seeing a
    traceback.

    Sabotage: remove the ``try/except`` around the ``fact_store.search``
    call in tool_facts_about → the RuntimeError propagates out of the
    tool, this test fails with the bare RuntimeError instead of the
    LookupFailed assertion. Mutate-confirmed.
    """
    store = _RaisingFactStore(message="db is missing")

    out = tool_facts_about(entity="Alice", fact_store=store, document_repo=_empty_doc_repo())

    assert out["error"] == ERROR_LOOKUP_FAILED
    assert "db is missing" in out["detail"]
    assert out["hits"] == []
    assert out["entity_summaries"] == []
    assert out["entity"] == "Alice"


# ---------------------------------------------------------------------------
# #431 — canonical-first ordering on facts_about
# ---------------------------------------------------------------------------


class _FakeStoreWithHits:
    """FactStore stand-in returning pre-canned hits + entity for sourcing."""

    def __init__(self, *, hits: list[Any] | None = None) -> None:
        self._hits = list(hits or [])

    def search(self, query: str, *, top_k: int = 10, namespace: str | None = None) -> list[Any]:
        del query, top_k, namespace
        return list(self._hits)


def test_facts_about_carries_canonical_payload_when_entity_matches_declaration() -> None:
    """When the looked-up entity matches a declared canonical, the
    response includes ``canonical: {name, type, summary, aliases}``.

    Sabotage-proof: drop the ``canonical_match`` block in
    ``tool_facts_about`` → the field stays ``None`` even on a match.
    """
    from kairix.knowledge.entities.canonical import CanonicalEntity

    canonicals = [
        CanonicalEntity(
            name="Acme Corp",
            entity_type="organisation",
            summary="Software vendor.",
            aliases=("Acme", "Acme Inc."),
        ),
    ]
    out = tool_facts_about(
        entity="Acme Corp",
        fact_store=_FakeStoreWithHits(),
        document_repo=_empty_doc_repo(),
        canonicals=canonicals,
    )
    assert out["canonical"] is not None
    assert out["canonical"]["name"] == "Acme Corp"
    assert out["canonical"]["type"] == "organisation"
    assert out["canonical"]["summary"] == "Software vendor."
    assert out["canonical"]["aliases"] == ["Acme", "Acme Inc."]


def test_facts_about_canonical_match_is_alias_aware_and_case_insensitive() -> None:
    """A lookup using an alias or different casing still resolves the
    canonical match."""
    from kairix.knowledge.entities.canonical import CanonicalEntity

    canonicals = [
        CanonicalEntity(
            name="Acme Corp",
            entity_type="organisation",
            summary="Vendor.",
            aliases=("Acme",),
        ),
    ]
    via_alias = tool_facts_about(
        entity="acme", fact_store=_FakeStoreWithHits(), document_repo=_empty_doc_repo(), canonicals=canonicals
    )
    via_name_case = tool_facts_about(
        entity="ACME CORP", fact_store=_FakeStoreWithHits(), document_repo=_empty_doc_repo(), canonicals=canonicals
    )
    assert via_alias["canonical"]["name"] == "Acme Corp"
    assert via_name_case["canonical"]["name"] == "Acme Corp"


def test_facts_about_canonical_field_is_none_for_unknown_entity() -> None:
    """An entity that doesn't match any declared canonical yields
    ``canonical: None`` — locks the no-false-positive contract."""
    from kairix.knowledge.entities.canonical import CanonicalEntity

    canonicals = [CanonicalEntity(name="Acme Corp", entity_type="organisation", summary="x")]
    out = tool_facts_about(
        entity="Some other company",
        fact_store=_FakeStoreWithHits(),
        document_repo=_empty_doc_repo(),
        canonicals=canonicals,
    )
    assert out["canonical"] is None


def test_facts_about_empty_canonicals_yields_canonical_none() -> None:
    """An empty canonicals list (no operator declarations) → response
    carries ``canonical: None`` for any entity — the no-canonicals
    deployment sees baseline behaviour."""
    out = tool_facts_about(
        entity="Anything", fact_store=_FakeStoreWithHits(), document_repo=_empty_doc_repo(), canonicals=[]
    )
    assert out["canonical"] is None
    assert out["error"] == ""


def test_facts_about_error_branches_carry_canonical_none() -> None:
    """The InvalidInput + LookupFailed envelopes both carry
    ``canonical: None`` so agents reading the field always find it
    present (not KeyError-prone)."""
    invalid_out = tool_facts_about(entity="", fact_store=_FakeStoreWithHits(), document_repo=_empty_doc_repo())
    assert invalid_out["error"] == ERROR_INVALID_INPUT
    assert invalid_out["canonical"] is None

    failed_out = tool_facts_about(
        entity="X", fact_store=_RaisingFactStore(message="oops"), document_repo=_empty_doc_repo()
    )
    assert failed_out["error"] == ERROR_LOOKUP_FAILED
    assert failed_out["canonical"] is None
