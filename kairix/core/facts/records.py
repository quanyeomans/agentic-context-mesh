"""Concrete ``FactRecord`` implementation: ``StoredFactRecord``.

Frozen dataclass satisfying the runtime-checkable ``FactRecord`` Protocol
in ``kairix.core.protocols``. Carries the deterministic-id minting helper
that pins the identity contract: same ``(entity, attribute,
source_turn_ids)`` triple → same id, order-independent on source turns.

The deterministic id is what makes ``FactStore.add`` safely idempotent —
re-running ingest over the same conversation window produces facts with
the same ids, so ``INSERT OR IGNORE`` collapses duplicates without
needing a separate de-dup pass.

See ``docs/architecture/fitness-functions.md`` for the F1/F5/F26 rules
this module respects. No imports of ``kairix.providers`` or
``kairix.transport`` (F26).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kairix.core.protocols import FactRecord


@dataclass(frozen=True)
class StoredFactRecord:
    """Frozen ``FactRecord`` implementation persisted by ``SQLiteFactStore``.

    All fields map 1:1 onto columns in the ``facts`` SQLite table. The
    ``source_turn_ids`` tuple is stored as a JSON array on the SQL side
    so order is preserved on round-trip; the deterministic-id helper
    (``mint_id``) sorts before hashing so re-ordering of source turns
    does not break idempotency.
    """

    id: str
    entity: str
    attribute: str
    value: str
    confidence: float
    source_turn_ids: tuple[str, ...]
    extracted_at: str  # ISO-8601 timestamp — wall-clock at extraction time
    superseded_by: str | None
    namespace: str
    # Stream A, Lever A — event-time temporal anchor (distinct from
    # ``extracted_at``). ``None`` for legacy rows or sessions ingested
    # without ``session_metadata`` carrying ``date_time``.
    evidence_at: str | None = None
    # PLA-261 — actionable provenance. ``conversation_id`` is the
    # grouping key of the transcript the fact was grounded in (populated
    # at extraction from the source turns); ``source_uri`` is the
    # resolvable pointer an agent can re-open to verify the fact
    # (populated at ingest from the conversation document path, or carried
    # through from a connector for federated provenance #429). Both are
    # ``None`` on legacy rows ingested before the breadcrumb shipped — the
    # read-time :func:`resolve_fact_source_uri` falls back gracefully.
    conversation_id: str | None = None
    source_uri: str | None = None

    @classmethod
    def mint_id(
        cls,
        *,
        entity: str,
        attribute: str,
        source_turn_ids: tuple[str, ...],
    ) -> str:
        """Compute the deterministic id for an ``(entity, attribute, turns)`` triple.

        ``source_turn_ids`` is sorted before hashing so the id is
        order-independent: a fact extracted from turns ``("t2", "t1")``
        gets the same id as one extracted from ``("t1", "t2")``. This
        matches the Protocol's identity contract documented in
        ``kairix.core.protocols.FactRecord``.

        Returns the first 16 hex chars of the sha256 digest — enough
        entropy for collision safety inside a single engagement (~10^9
        facts before birthday-collision risk) while staying short
        enough to read in a log line.
        """
        sorted_turns = sorted(source_turn_ids)
        payload = entity + "|" + attribute + "|" + "|".join(sorted_turns)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return digest[:16]


def resolve_fact_source_uri(record: FactRecord) -> str:
    """Return the canonical, resolvable ``source_uri`` for one fact (PLA-261).

    The single read-time breadcrumb resolver, shared by the ``facts_about``
    read surface and the SearchPipeline fact-federation path so neither
    hand-rolls its own pointer (F17). Resolution order, each step a
    *resolvable* pointer:

    1. ``record.source_uri`` when stored — the authoritative provenance
       (the conversation document path stamped at ingest, or a connector
       URI carried through for federated facts #429).
    2. ``record.conversation_id`` → the conversation document's relative
       path via :func:`kairix.paths.agent_conversation_doc_rel_path`. Covers
       facts that carry the grouping key but were never ingest-stamped
       (e.g. a direct extractor call).
    3. ``facts://<id>`` — the last-resort self-pointer for legacy rows that
       predate the breadcrumb. Still namespaces the fact distinctly; never
       empty, so the SLO "100% of results carry a source_uri" holds.

    ``getattr`` reads keep the resolver tolerant of duck-typed
    FactRecord-shaped objects that predate the provenance fields.
    """
    explicit = (getattr(record, "source_uri", None) or "").strip()
    if explicit:
        return explicit
    conversation_id = getattr(record, "conversation_id", None)
    if conversation_id:
        from kairix.paths import agent_conversation_doc_rel_path

        return agent_conversation_doc_rel_path(str(conversation_id))
    return f"facts://{record.id}"
