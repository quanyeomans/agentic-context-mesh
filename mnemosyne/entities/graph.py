"""
Entity graph CRUD for Mnemosyne.

Provides write, lookup, and mention tracking for the entities SQLite database.
All DB operations require an open connection from open_entities_db().

Vault root: /data/obsidian-vault/
  Entity markdown files are created relative to this root when not found.
"""

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

VAULT_ROOT = Path("/data/obsidian-vault")


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class EntityResult:
    """Result returned by entity_lookup()."""

    id: str
    type: str
    name: str
    summary: str | None
    markdown_path: str
    facts: list[dict] = field(default_factory=list)
    mentions: list[str] = field(default_factory=list)
    relationships: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def slug(name: str) -> str:
    """
    Convert a human name to a kebab-case slug.

    Examples:
        "Alice Chen"  → "alice-chen"
        "Acme Corp"  → "acme-corp"
        "platform/qmd" → "platform-qmd"
    """
    # Lowercase
    result = name.lower()
    # Replace any non-alphanumeric character (except hyphen) with a hyphen
    result = re.sub(r"[^a-z0-9]+", "-", result)
    # Strip leading/trailing hyphens and collapse multiple hyphens
    result = result.strip("-")
    result = re.sub(r"-{2,}", "-", result)
    return result


def _now_iso() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today() -> str:
    """Return today's date as YYYY-MM-DD."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def entity_write(
    name: str,
    entity_type: str,
    markdown_path: str,
    db: sqlite3.Connection,
    facts: list[str] | None = None,
    summary: str | None = None,
) -> str:
    """
    Create or update an entity in the entities DB.

    Uses INSERT OR REPLACE so repeated calls are idempotent (upsert by id).
    If facts are provided, they are appended to entity_facts.
    Creates the vault Markdown file if it does not already exist.

    Args:
        name:          Human-readable entity name (e.g. "Alice Chen").
        entity_type:   One of: person, organisation, decision, concept, project.
        markdown_path: Path to vault Markdown file, relative to VAULT_ROOT.
        db:            Open entities DB connection.
        facts:         Optional list of fact strings to add (e.g. ["CEO, 2024"]).
        summary:       Optional one-line summary for the entity.

    Returns:
        The entity id (slug derived from name).
    """
    entity_id = slug(name)
    now = _now_iso()

    db.execute(
        """
        INSERT OR REPLACE INTO entities
            (id, type, name, status, markdown_path, summary, agent_scope, created_at, updated_at)
        VALUES (?, ?, ?, 'active', ?, ?, 'shared', ?, ?)
        """,
        (entity_id, entity_type, name, markdown_path, summary, now, now),
    )

    if facts:
        for fact_text in facts:
            db.execute(
                """
                INSERT INTO entity_facts
                    (entity_id, fact_type, fact_text, created_at)
                VALUES (?, 'other', ?, ?)
                """,
                (entity_id, fact_text, now),
            )

    db.commit()

    # Create vault Markdown file if it doesn't exist
    vault_file = VAULT_ROOT / markdown_path
    if not vault_file.exists():
        vault_file.parent.mkdir(parents=True, exist_ok=True)
        content = _render_vault_template(
            entity_type=entity_type,
            name=name,
            entity_id=entity_id,
            summary=summary,
        )
        vault_file.write_text(content, encoding="utf-8")

    return entity_id


def _render_vault_template(
    entity_type: str,
    name: str,
    entity_id: str,
    summary: str | None,
) -> str:
    """Render the Obsidian vault Markdown template for a new entity."""
    body = summary if summary else "Entity file. Add details here."
    today = _today()
    return (
        f"---\ntype: {entity_type}\nname: {name}\nentity-id: {entity_id}\ncreated: {today}\n---\n\n# {name}\n\n{body}\n"
    )


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def entity_lookup(name: str, db: sqlite3.Connection) -> EntityResult | None:
    """
    Look up an entity by name.

    First tries an exact match on entities.name, then falls back to a LIKE
    match (%name%). Returns the first match with associated facts, mentions,
    and relationships, or None if not found.

    Args:
        name: Name to search for.
        db:   Open entities DB connection.

    Returns:
        EntityResult or None.
    """
    # Exact match first
    row = db.execute(
        "SELECT id, type, name, summary, markdown_path FROM entities WHERE name = ? AND status = 'active'",
        (name,),
    ).fetchone()

    if row is None:
        # Fuzzy LIKE match
        row = db.execute(
            "SELECT id, type, name, summary, markdown_path FROM entities WHERE name LIKE ? AND status = 'active'",
            (f"%{name}%",),
        ).fetchone()

    if row is None:
        return None

    entity_id = row[0]

    # Load facts ordered by valid_from DESC (NULLs last)
    fact_rows = db.execute(
        """
        SELECT fact_type, fact_text, valid_from, valid_until, source_ref, created_at
        FROM entity_facts
        WHERE entity_id = ?
        ORDER BY valid_from DESC NULLS LAST, created_at DESC
        """,
        (entity_id,),
    ).fetchall()
    facts = [
        {
            "fact_type": r[0],
            "fact_text": r[1],
            "valid_from": r[2],
            "valid_until": r[3],
            "source_ref": r[4],
            "created_at": r[5],
        }
        for r in fact_rows
    ]

    # Top-5 mentions
    mentions = get_mentions(entity_id, db, limit=5)

    # Relationships
    rel_rows = db.execute(
        """
        SELECT r.id, r.from_entity, r.to_entity, r.rel_type, r.label, r.confidence, r.source_ref
        FROM relationships r
        WHERE r.from_entity = ? OR r.to_entity = ?
        ORDER BY r.created_at DESC
        """,
        (entity_id, entity_id),
    ).fetchall()
    relationships = [
        {
            "id": r[0],
            "from_entity": r[1],
            "to_entity": r[2],
            "rel_type": r[3],
            "label": r[4],
            "confidence": r[5],
            "source_ref": r[6],
        }
        for r in rel_rows
    ]

    return EntityResult(
        id=row[0],
        type=row[1],
        name=row[2],
        summary=row[3],
        markdown_path=row[4],
        facts=facts,
        mentions=mentions,
        relationships=relationships,
    )


def get_mentions(entity_id: str, db: sqlite3.Connection, limit: int = 10) -> list[str]:
    """
    Return doc_uris where this entity was mentioned, ordered by mention_count DESC, last_seen DESC.

    Args:
        entity_id: Entity id (slug).
        db:        Open entities DB connection.
        limit:     Max results to return (default 10).

    Returns:
        List of doc_uri strings.
    """
    rows = db.execute(
        """
        SELECT doc_uri
        FROM entity_mentions
        WHERE entity_id = ?
        ORDER BY mention_count DESC, last_seen DESC
        LIMIT ?
        """,
        (entity_id, limit),
    ).fetchall()
    return [r[0] for r in rows]


# ---------------------------------------------------------------------------
# Schema v2 — vault_path, frequency, relationships, canonical aliases
# ---------------------------------------------------------------------------


def set_vault_path(entity_id: int, vault_path: str, db: sqlite3.Connection) -> None:
    """Set the vault_path for an entity."""
    db.execute(
        "UPDATE entities SET vault_path = ? WHERE id = ?",
        (vault_path, entity_id),
    )
    db.commit()


def get_by_vault_path(vault_path: str, db: sqlite3.Connection) -> dict | None:
    """Look up entity by vault_path."""
    row = db.execute(
        "SELECT id, type, name, summary, markdown_path, vault_path, status, frequency, last_seen, canonical_id"
        " FROM entities WHERE vault_path = ?",
        (vault_path,),
    ).fetchone()
    if row is None:
        return None
    return dict(row)


def update_frequency(entity_id: int, db: sqlite3.Connection) -> None:
    """Increment mention count and update last_seen to today."""
    today = _today()
    db.execute(
        "UPDATE entities SET frequency = frequency + 1, last_seen = ? WHERE id = ?",
        (today, entity_id),
    )
    db.commit()


def write_relationship(
    entity_a_id: int,
    entity_b_id: int,
    relationship_type: str,
    strength: float,
    db: sqlite3.Connection,
) -> None:
    """Upsert a relationship between two entities."""
    today = _today()
    db.execute(
        """
        INSERT INTO entity_relationships
            (entity_a_id, entity_b_id, relationship_type, strength, last_updated)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(entity_a_id, entity_b_id, relationship_type)
        DO UPDATE SET strength = excluded.strength, last_updated = excluded.last_updated
        """,
        (entity_a_id, entity_b_id, relationship_type, strength, today),
    )
    db.commit()


def get_relationships(entity_id: int, db: sqlite3.Connection) -> list[dict]:
    """Return all relationships for an entity (both directions)."""
    rows = db.execute(
        """
        SELECT id, entity_a_id, entity_b_id, relationship_type, strength, last_updated
        FROM entity_relationships
        WHERE entity_a_id = ? OR entity_b_id = ?
        ORDER BY strength DESC, last_updated DESC
        """,
        (entity_id, entity_id),
    ).fetchall()
    return [dict(r) for r in rows]


def set_canonical(alias_id: int, canonical_id: int, db: sqlite3.Connection) -> None:
    """Mark an entity as an alias pointing to a canonical entity."""
    db.execute(
        "UPDATE entities SET canonical_id = ? WHERE id = ?",
        (canonical_id, alias_id),
    )
    db.commit()


def get_canonical(entity_id: int, db: sqlite3.Connection) -> dict | None:
    """Return the canonical entity for an alias (or self if already canonical).

    Follows one level of canonical_id. If the entity has no canonical_id set,
    returns the entity itself.
    """
    row = db.execute(
        "SELECT id, type, name, summary, markdown_path, vault_path, status, frequency, last_seen, canonical_id"
        " FROM entities WHERE id = ?",
        (entity_id,),
    ).fetchone()
    if row is None:
        return None
    entity = dict(row)
    if entity["canonical_id"] is None:
        return entity
    # Follow the canonical pointer
    canon_row = db.execute(
        "SELECT id, type, name, summary, markdown_path, vault_path, status, frequency, last_seen, canonical_id"
        " FROM entities WHERE id = ?",
        (entity["canonical_id"],),
    ).fetchone()
    return dict(canon_row) if canon_row else entity


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


def entity_list(db: sqlite3.Connection, entity_type: str | None = None) -> list[EntityResult]:
    """
    List all active entities, optionally filtered by type.

    Args:
        db:          Open entities DB connection.
        entity_type: Optional type filter (e.g. 'person').

    Returns:
        List of EntityResult (no facts/mentions/relationships loaded for efficiency).
    """
    if entity_type:
        rows = db.execute(
            "SELECT id, type, name, summary, markdown_path FROM entities"
            " WHERE status = 'active' AND type = ? ORDER BY name",
            (entity_type,),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT id, type, name, summary, markdown_path FROM entities WHERE status = 'active' ORDER BY type, name"
        ).fetchall()

    return [
        EntityResult(
            id=r[0],
            type=r[1],
            name=r[2],
            summary=r[3],
            markdown_path=r[4],
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Graph context for query planning
# ---------------------------------------------------------------------------


def graph_context(query: str, db: sqlite3.Connection, max_entities: int = 3) -> str | None:
    """
    Return a brief entity relationship context string for injection into the planner prompt.

    Tokenises the query, matches entity names in the DB, loads their relationships,
    and returns a formatted bullet list. Returns None if no relevant entities found
    or on any error (silent degradation — never raises).

    Example output:
      Entity context:
      - Alice Chen (person): LEADS engineering, WORKS_AT Acme Corp
      - Acme Corp (organisation): FOUNDED_BY Alice Chen, PARTNER_OF Beta Ltd
    """
    try:
        # Tokenise query: words >= 4 chars, deduped, preserve order
        words = list(dict.fromkeys(
            w for w in re.findall(r"[A-Za-z]{4,}", query)
        ))
        if not words:
            return None

        # Find matching entities (LIKE match on name, up to 5 candidates)
        placeholders = " OR ".join("name LIKE ?" for _ in words)
        params = [f"%{w}%" for w in words]
        rows = db.execute(
            f"SELECT id, type, name FROM entities WHERE status = 'active' AND ({placeholders}) LIMIT 5",
            params,
        ).fetchall()

        if not rows:
            return None

        lines = []
        seen_entities = set()
        for entity_id, entity_type, entity_name in rows:
            if entity_id in seen_entities or len(lines) >= max_entities:
                break
            seen_entities.add(entity_id)

            # Load relationships for this entity
            rels = db.execute(
                """
                SELECT er.relationship_type, e.name
                FROM entity_relationships er
                JOIN entities e ON e.id = er.entity_b_id
                WHERE er.entity_a_id = ?
                LIMIT 6
                """,
                (entity_id,),
            ).fetchall()

            if not rels:
                continue  # Skip entities with no relationships (no useful context)

            rel_parts = [f"{rel_type} {target_name}" for rel_type, target_name in rels]
            lines.append(f"- {entity_name} ({entity_type}): {', '.join(rel_parts)}")

        if not lines:
            return None

        return "Entity context:\n" + "\n".join(lines)

    except Exception:
        return None

