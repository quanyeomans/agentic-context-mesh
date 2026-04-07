"""
Entity → wikilink resolution for Mnemosyne.

Loads WikiEntity records from entities.db (preferred) or the bootstrap
wikilink-entity-index.md (fallback).

Entity sources:
  Primary: /data/mnemosyne/entities.db  (vault_path column, v2 schema)
  Fallback: <vault-root>/agent-knowledge/shared/wikilink-entity-index.md
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

DEFAULT_DB_PATH = "/data/mnemosyne/entities.db"
DEFAULT_BOOTSTRAP_PATH = "<vault-root>/agent-knowledge/shared/wikilink-entity-index.md"

# Minimum number of DB entities with vault_path required before we prefer DB
_DB_THRESHOLD = 5


@dataclass
class WikiEntity:
    """An entity that can be linked in vault markdown files."""

    name: str
    aliases: list[str]  # all names/aliases (including name itself) that trigger this link
    vault_path: str  # e.g. "02-Areas/Clients/Acme-Corp/"
    link: str  # e.g. "[[Acme-Corp]]" or "[[Gamma-Systems|Gamma Systems]]"
    entity_type: str  # organisation, person, project, tool, etc.

    def all_triggers(self) -> list[str]:
        """Return all text strings (name + aliases) that should trigger this wikilink."""
        seen: set[str] = set()
        result: list[str] = []
        for term in [self.name, *self.aliases]:
            if term and term not in seen:
                seen.add(term)
                result.append(term)
        return result


# ---------------------------------------------------------------------------
# DB loader
# ---------------------------------------------------------------------------


def load_entities_from_db(db_path: str = DEFAULT_DB_PATH) -> list[WikiEntity]:
    """
    Load entities with vault_path from entities.db.

    Canonical entities (canonical_id IS NULL) with a vault_path are loaded as
    primary WikiEntity entries.  Alias entities (canonical_id IS NOT NULL) are
    merged into their canonical's aliases list so that alias surface forms
    trigger the canonical [[link]] in vault files.

    Returns empty list if the DB is unavailable or has no matching rows.
    """
    try:
        db = sqlite3.connect(db_path)
        db.row_factory = sqlite3.Row
        # Check schema has vault_path column (v2+)
        cols = {row[1] for row in db.execute("PRAGMA table_info(entities)").fetchall()}
        if "vault_path" not in cols:
            db.close()
            return []

        has_canonical_id = "canonical_id" in cols

        if has_canonical_id:
            # Load canonical entities (vault_path set, canonical_id NULL or missing)
            canonical_rows = db.execute(
                """
                SELECT id, name, type, vault_path
                FROM entities
                WHERE vault_path IS NOT NULL AND vault_path != ''
                  AND canonical_id IS NULL
                """
            ).fetchall()

            # Load alias entities (canonical_id IS NOT NULL)
            alias_rows = db.execute(
                """
                SELECT name, canonical_id
                FROM entities
                WHERE canonical_id IS NOT NULL
                """
            ).fetchall()
        else:
            # Older schema without canonical_id — load all with vault_path
            canonical_rows = db.execute(
                """
                SELECT id, name, type, vault_path
                FROM entities
                WHERE vault_path IS NOT NULL AND vault_path != ''
                """
            ).fetchall()
            alias_rows = []

        db.close()
    except Exception:
        return []

    # Build map: canonical_id → list of alias names
    alias_map: dict[str, list[str]] = {}
    for arow in alias_rows:
        cid = str(arow["canonical_id"])
        alias_map.setdefault(cid, []).append(arow["name"])

    entities: list[WikiEntity] = []
    for row in canonical_rows:
        name: str = row["name"]
        entity_id: str = row["id"]
        entity_type: str = row["type"] or "unknown"
        vault_path: str = row["vault_path"]
        link = _make_link(name)
        # Attach any alias names from alias_map
        aliases = alias_map.get(entity_id, [])
        entities.append(
            WikiEntity(
                name=name,
                aliases=aliases,
                vault_path=vault_path,
                link=link,
                entity_type=entity_type,
            )
        )
    return entities


def _make_link(name: str) -> str:
    """Build a plain [[name]] wikilink. No alias needed for simple names."""
    return f"[[{name}]]"


# ---------------------------------------------------------------------------
# Bootstrap loader
# ---------------------------------------------------------------------------

# Matches table rows like:
#   | Acme-Corp | `[[Acme-Corp]]` | `02-Areas/Clients/Acme-Corp/` |
#   | Gamma Systems | `[[Gamma-Systems\|Gamma Systems]]` | `02-Areas/Clients/Gamma-Systems/` |
_TABLE_ROW_RE = re.compile(r"^\|\s*(?P<entity>[^|]+?)\s*\|\s*`(?P<link>\[\[[^\]]+\]\])`\s*\|\s*`(?P<path>[^`]+)`\s*\|")


def load_entities_from_bootstrap(
    index_path: str = DEFAULT_BOOTSTRAP_PATH,
) -> list[WikiEntity]:
    """
    Parse the bootstrap wikilink index markdown table.

    Handles tables from all sections (Clients, Organisations, Projects, etc.).
    Skips header rows, section headers, and malformed lines.

    Parses rows like:
      | Acme-Corp | `[[Acme-Corp]]` | `02-Areas/Clients/Acme-Corp/` |
      | Gamma Systems | `[[Gamma-Systems\\|Gamma Systems]]` | `02-Areas/Clients/Gamma-Systems/` |
    """
    try:
        with open(index_path, encoding="utf-8") as fh:
            content = fh.read()
    except OSError:
        return []

    entities: list[WikiEntity] = []
    seen_names: set[str] = set()

    # Determine section context for entity_type
    current_section = "unknown"
    section_map = {
        "clients": "organisation",
        "key organisations": "organisation",
        "active projects": "project",
        "frameworks": "framework",
        "key people": "person",
        "agent platform": "component",
    }

    for line in content.splitlines():
        # Update section from H2 headers
        if line.startswith("## "):
            heading = line[3:].strip().lower()
            for keyword, etype in section_map.items():
                if keyword in heading:
                    current_section = etype
                    break

        m = _TABLE_ROW_RE.match(line)
        if not m:
            continue

        entity_name = m.group("entity").strip()
        raw_link = m.group("link").strip()
        vault_path = m.group("path").strip()

        # Skip header rows
        if entity_name.lower() in ("entity", "name"):
            continue
        # Skip vault path annotations like "(general reference)" - keep just path part
        # Strip trailing parenthetical notes from vault_path
        vault_path = re.sub(r"\s*\(.*?\)\s*$", "", vault_path).strip()
        if not vault_path or not entity_name:
            continue

        # Unescape \| inside wikilinks (markdown table escaping)
        link = raw_link.replace("\\|", "|")

        # Extract aliases from display text in the link [[target|display]]
        aliases = _extract_aliases(entity_name, link)

        if entity_name in seen_names:
            continue
        seen_names.add(entity_name)

        entities.append(
            WikiEntity(
                name=entity_name,
                aliases=aliases,
                vault_path=vault_path,
                link=link,
                entity_type=current_section,
            )
        )

    return entities


def _extract_aliases(entity_name: str, link: str) -> list[str]:
    """
    Extract alternate trigger strings from the wikilink.

    For [[Gamma-Systems|Gamma Systems]], the display text 'Gamma Systems' is an alias.
    Always excludes entity_name itself (it's the primary trigger).
    """
    aliases: list[str] = []
    # Match [[target|display]] or [[target]]
    m = re.match(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", link)
    if not m:
        return aliases
    target = m.group(1)  # e.g. "Gamma-Systems"
    display = m.group(2)  # e.g. "Gamma Systems" or None

    candidates = []
    if target and target != entity_name:
        candidates.append(target)
    if display and display != entity_name:
        candidates.append(display)

    seen = {entity_name}
    for c in candidates:
        if c not in seen:
            seen.add(c)
            aliases.append(c)

    return aliases


# ---------------------------------------------------------------------------
# Unified loader
# ---------------------------------------------------------------------------


def get_entities(prefer_db: bool = True) -> list[WikiEntity]:
    """
    Load entities from DB (preferred) or bootstrap index (fallback).

    Falls back to bootstrap if DB has fewer than _DB_THRESHOLD entities with vault_path.
    """
    if prefer_db:
        db_entities = load_entities_from_db()
        if len(db_entities) >= _DB_THRESHOLD:
            return db_entities

    # Fallback to bootstrap
    return load_entities_from_bootstrap()
