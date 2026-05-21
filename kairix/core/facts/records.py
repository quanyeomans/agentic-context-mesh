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
