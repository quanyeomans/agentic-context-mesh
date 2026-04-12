"""
Mnemosyne ontology reconciler — prevents duplicate entities.

Resolves extracted entity names against the existing ontology via:
1. Exact name match
2. Alias match (entities with canonical_id pointing to another entity)
3. Embedding similarity (if available; falls back gracefully)

Auto-merges when similarity >= auto_merge_threshold (default 0.92).
Logs candidates below threshold for manual review.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from datetime import datetime, timezone

from kairix.entities.extract import ExtractedEntity
from kairix.entities.resolver import resolve_canonical

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today() -> str:
    """Return today's date as YYYY-MM-DD."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _slug(name: str) -> str:
    """Convert a name to a kebab-case slug (mirrors graph.slug())."""
    import re

    result = name.lower()
    result = re.sub(r"[^a-z0-9]+", "-", result)
    result = result.strip("-")
    result = re.sub(r"-{2,}", "-", result)
    return result


def _string_similarity(a: str, b: str) -> float:
    """
    Simple normalised character bigram similarity (Sorensen-Dice coefficient).
    Falls back gracefully when embeddings are unavailable.
    Range: 0.0 (no overlap) to 1.0 (identical).
    """
    a = a.lower().strip()
    b = b.lower().strip()
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0

    def bigrams(s: str) -> set[str]:
        return {s[i : i + 2] for i in range(len(s) - 1)}

    a_bi = bigrams(a)
    b_bi = bigrams(b)
    if not a_bi and not b_bi:
        return 1.0
    if not a_bi or not b_bi:
        return 0.0
    intersection = len(a_bi & b_bi)
    return (2 * intersection) / (len(a_bi) + len(b_bi))


def _get_embedding_similarity(
    name: str,
    db: sqlite3.Connection,
) -> list[tuple[str, float]]:
    """
    Attempt to find similar entities via embedding vectors.

    Returns list of (entity_id, similarity) sorted by similarity DESC.
    Falls back to empty list if embedding table/column not available.
    """
    # Embedding similarity requires the vec_items / embeddings table from QMD.
    # For now, entities.db does not store embeddings — use string similarity fallback.
    # This is a documented extension point for Phase 3.
    return []


# ---------------------------------------------------------------------------
# Canonical lookup
# ---------------------------------------------------------------------------


def find_canonical(
    name: str,
    db: sqlite3.Connection,
    similarity_threshold: float = 0.85,
) -> str | None:
    """
    Find existing entity ID for a name, checking:
    1. Exact name match on entities.name
    2. Alias match (entities where canonical_id is set, matched by name)
    3. String similarity against all entity names (bigram Dice coefficient)
       — used as a proxy for embedding similarity when embeddings unavailable.

    Args:
        name:                 Entity name to look up.
        db:                   Open entities DB connection.
        similarity_threshold: Minimum similarity to consider a match (0.0-1.0).

    Returns:
        entity_id (TEXT slug) of the canonical entity, or None if no match found.
    """
    # 1. Exact name match
    row = db.execute(
        "SELECT id, canonical_id FROM entities WHERE LOWER(name) = LOWER(?) LIMIT 1",
        (name,),
    ).fetchone()
    if row is not None:
        # If this entity is itself an alias, follow canonical_id
        if row["canonical_id"] is not None:
            return str(row["canonical_id"])
        return str(row["id"])

    # 2. Alias match — entities whose name matches but have canonical_id set
    # (canonical_id entities are "alias" records pointing to the real entity)
    alias_row = db.execute(
        """
        SELECT canonical_id FROM entities
        WHERE LOWER(name) = LOWER(?) AND canonical_id IS NOT NULL
        LIMIT 1
        """,
        (name,),
    ).fetchone()
    if alias_row is not None:
        return str(alias_row["canonical_id"])

    # 3. Similarity check
    if similarity_threshold >= 1.0:
        return None  # strict exact-only mode

    try:
        all_rows = db.execute("SELECT id, name, canonical_id FROM entities WHERE status = 'active'").fetchall()
    except sqlite3.OperationalError:
        return None

    best_id: str | None = None
    best_score = 0.0

    for row in all_rows:
        score = _string_similarity(name, row["name"])
        if score > best_score:
            best_score = score
            # Follow canonical_id if this is an alias
            best_id = str(row["canonical_id"]) if row["canonical_id"] else str(row["id"])

    if best_score >= similarity_threshold:
        return best_id

    return None


# ---------------------------------------------------------------------------
# Reconciler
# ---------------------------------------------------------------------------


def reconcile_extracted(
    extracted: list[ExtractedEntity],
    db: sqlite3.Connection,
    auto_merge_threshold: float = 0.92,
) -> dict[str, str]:
    """
    Reconcile a list of extracted entities against the ontology.

    For each entity:
    - If canonical match found at score >= auto_merge_threshold: merge
      (update alias record if needed, increment frequency).
    - If match found below threshold (but >= 0.5): log as candidate for
      manual review without writing.
    - If no match (score < 0.5): create new entity record.

    Args:
        extracted:            List of ExtractedEntity from the NER pipeline.
        db:                   Open entities DB connection.
        auto_merge_threshold: Similarity threshold for auto-merge (0.0-1.0).

    Returns:
        Mapping of {entity_name: entity_id} for all processed entities.
    """
    result: dict[str, str] = {}

    for entity in extracted:
        name = entity.name.strip()
        if not name:
            continue

        # Step 0: Resolve alias → canonical via resolve_canonical().
        # This catches seeded alias rows (e.g. "SME&C" → canonical "smec") before
        # the slug-based find_canonical() similarity search runs.
        canonical_row = resolve_canonical(name, db)
        if canonical_row is not None:
            canonical_id = canonical_row["id"]
            _increment_frequency(canonical_id, db)
            result[name] = canonical_id
            logger.debug("reconcile: alias resolved '%s' → canonical '%s'", name, canonical_id)
            continue

        # Check for exact/alias/similarity match
        canonical_id = find_canonical(name, db, similarity_threshold=auto_merge_threshold)

        if canonical_id is not None:
            # Auto-merge: update frequency
            _increment_frequency(canonical_id, db)
            result[name] = canonical_id
            logger.debug("reconcile: merged '%s' → entity '%s'", name, canonical_id)
            continue

        # Check for review-candidate (match between 0.5 and threshold)
        review_id = find_canonical(name, db, similarity_threshold=0.5)
        if review_id is not None:
            # Log for manual review but still track the name
            logger.info(
                "reconcile: merge candidate '%s' -> entity '%s' "
                "(below auto-merge threshold %.2f) -- manual review needed",
                name,
                review_id,
                auto_merge_threshold,
            )
            # Return the candidate ID so mentions can still be linked
            # (conservative: better to link to a near-match than create a dupe)
            result[name] = review_id
            continue

        # No match — create new entity
        new_id = _create_entity(entity, db)
        result[name] = new_id
        logger.debug("reconcile: created new entity '%s' (id=%s)", name, new_id)

    return result


def _increment_frequency(entity_id: str, db: sqlite3.Connection) -> None:
    """Increment frequency and update last_seen for an entity."""
    today = _today()
    try:
        db.execute(
            "UPDATE entities SET frequency = frequency + 1, last_seen = ? WHERE id = ?",
            (today, entity_id),
        )
        db.commit()
    except sqlite3.OperationalError:
        # frequency/last_seen columns may not exist in v1 schema — graceful fallback
        pass


def _create_entity(entity: ExtractedEntity, db: sqlite3.Connection) -> str:
    """
    Create a new entity record in entities.db.

    Maps extractor entity_type to DB type (limited to schema CHECK constraint values).
    Returns the new entity_id.
    """
    # Map extractor types to DB-constrained types
    type_map = {
        "person": "person",
        "org": "organisation",
        "organisation": "organisation",
        "project": "project",
        "framework": "concept",  # No 'framework' type in v1 schema — use concept
        "product": "concept",  # No 'product' type in v1 schema — use concept
        "place": "concept",  # No 'place' type in v1 schema — use concept
        "concept": "concept",
    }
    db_type = type_map.get(entity.entity_type, "concept")

    entity_id = _slug(entity.name)
    # Ensure uniqueness: if slug already taken, append a short hash
    existing = db.execute("SELECT id FROM entities WHERE id = ?", (entity_id,)).fetchone()
    if existing is not None:
        h = hashlib.md5(entity.name.encode()).hexdigest()[:4]  # noqa: S324  # nosec B324
        entity_id = f"{entity_id}-{h}"

    now = _now_iso()
    today = _today()
    markdown_path = f"06-Entities/{db_type}/{entity_id}.md"

    try:
        db.execute(
            """
            INSERT INTO entities
                (id, type, name, status, markdown_path, summary, agent_scope, created_at, updated_at,
                 frequency, last_seen)
            VALUES (?, ?, ?, 'active', ?, NULL, 'shared', ?, ?, 1, ?)
            """,
            (entity_id, db_type, entity.name, markdown_path, now, now, today),
        )
    except sqlite3.OperationalError:
        # Fallback for v1 schema without frequency/last_seen columns
        db.execute(
            """
            INSERT OR IGNORE INTO entities
                (id, type, name, status, markdown_path, summary, agent_scope, created_at, updated_at)
            VALUES (?, ?, ?, 'active', ?, NULL, 'shared', ?, ?)
            """,
            (entity_id, db_type, entity.name, markdown_path, now, now),
        )

    db.commit()
    return entity_id


# ---------------------------------------------------------------------------
# Mention tracking
# ---------------------------------------------------------------------------


def update_mentions(
    entity_ids: list[str],
    doc_hash: str,
    doc_path: str,
    db: sqlite3.Connection,
) -> None:
    """
    Write entity_mentions rows for a document.

    Uses INSERT OR IGNORE to avoid duplicate rows. On conflict (already tracked),
    increments mention_count and updates last_seen.
    Also increments frequency for each entity.

    Args:
        entity_ids:  List of entity ID slugs to link.
        doc_hash:    Content hash of the document (used as part of doc_uri key).
        doc_path:    Vault path of the document (stored as doc_uri).
        db:          Open entities DB connection.
    """
    now = _now_iso()
    # Use doc_path as the URI (canonical identifier for the document)
    doc_uri = doc_path

    for entity_id in entity_ids:
        try:
            # Attempt upsert: insert or update on conflict
            db.execute(
                """
                INSERT INTO entity_mentions (entity_id, doc_uri, mention_count, first_seen, last_seen)
                VALUES (?, ?, 1, ?, ?)
                ON CONFLICT(entity_id, doc_uri)
                DO UPDATE SET
                    mention_count = mention_count + 1,
                    last_seen = excluded.last_seen
                """,
                (entity_id, doc_uri, now, now),
            )
        except sqlite3.OperationalError:
            # Fallback for SQLite versions without ON CONFLICT DO UPDATE (< 3.24)
            existing = db.execute(
                "SELECT mention_count FROM entity_mentions WHERE entity_id = ? AND doc_uri = ?",
                (entity_id, doc_uri),
            ).fetchone()
            if existing is None:
                db.execute(
                    """
                    INSERT OR IGNORE INTO entity_mentions
                        (entity_id, doc_uri, mention_count, first_seen, last_seen)
                    VALUES (?, ?, 1, ?, ?)
                    """,
                    (entity_id, doc_uri, now, now),
                )
            else:
                db.execute(
                    """
                    UPDATE entity_mentions
                    SET mention_count = mention_count + 1, last_seen = ?
                    WHERE entity_id = ? AND doc_uri = ?
                    """,
                    (now, entity_id, doc_uri),
                )

        # Update frequency on entity record
        _increment_frequency(entity_id, db)

    db.commit()
