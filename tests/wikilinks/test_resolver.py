"""
Tests for kairix.wikilinks.resolver

Covers:
- load_entities_from_bootstrap(): parses a synthetic index file (tmp_path fixture)
- load_entities_from_db(): loads from a synthetic SQLite DB (tmp_path fixture)
- get_entities(): DB-prefer / fallback logic via monkeypatch

All tests are fully self-contained — no real vault or DB required.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kairix.wikilinks.resolver import (
    WikiEntity,
    get_entities,
    load_entities_from_bootstrap,
    load_entities_from_db,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

SYNTHETIC_BOOTSTRAP = """\
# Wikilink Entity Index

## Clients

| Entity | Link | Vault Path |
|---|---|---|
| Acme Corp | `[[Acme-Corp]]` | `02-Areas/Clients/Acme-Corp/` |
| Zenith Ltd | `[[Zenith-Ltd]]` | `02-Areas/Clients/Zenith-Ltd/` |
| Gamma Systems | `[[Gamma-Systems\\|Gamma Systems]]` | `02-Areas/Clients/Gamma-Systems/` |
| Delta Co | `[[Delta-Co]]` | `02-Areas/Clients/Delta-Co/` |

## Key Organisations

| Entity | Link | Vault Path |
|---|---|---|
| Nexus Digital | `[[NexusDigital]]` | `02-Areas/Work/Orgs/NexusDigital/` |
| Softcorp | `[[Softcorp]]` | `02-Areas/Work/Orgs/Softcorp/` |

## Key People

| Entity | Link | Vault Path |
|---|---|---|
| Alice Chen | `[[AliceChen]]` | `02-Areas/People/AliceChen/` |
| Sam Rivera | `[[SamRivera]]` | `02-Areas/People/SamRivera/` |

## Active Projects

| Entity | Link | Vault Path |
|---|---|---|
| Project Atlas | `[[ProjectAtlas]]` | `01-Projects/Atlas/` |
| Project Beacon | `[[ProjectBeacon]]` | `01-Projects/Beacon/` |

## Frameworks

| Entity | Link | Vault Path |
|---|---|---|
| Triad Method | `[[TriadMethod]]` | `05-Knowledge/Frameworks/TriadMethod/` |
| Relay Framework | `[[RelayFramework]]` | `05-Knowledge/Frameworks/RelayFramework/` |
"""


@pytest.fixture()
def bootstrap_file(tmp_path: Path) -> str:
    """Write synthetic bootstrap index to a tmp file and return its path."""
    p = tmp_path / "wikilink-entity-index.md"
    p.write_text(SYNTHETIC_BOOTSTRAP, encoding="utf-8")
    return str(p)


@pytest.fixture()
def synthetic_db(tmp_path: Path) -> str:
    """Create a synthetic entities.db with vault_path column (v2 schema)."""
    db_path = str(tmp_path / "entities.db")
    db = sqlite3.connect(db_path)
    db.execute(
        "CREATE TABLE entities ("
        "id TEXT PRIMARY KEY, name TEXT NOT NULL, type TEXT NOT NULL, "
        "markdown_path TEXT NOT NULL, status TEXT DEFAULT 'active', "
        "agent_scope TEXT DEFAULT 'shared', "
        "created_at TEXT, updated_at TEXT, vault_path TEXT, canonical_id TEXT)"
    )
    rows = [
        (
            "acme-health",
            "Acme Corp",
            "client",
            "path",
            "active",
            "shared",
            "2024-01-01",
            "2024-01-01",
            "02-Areas/Clients/Acme-Corp/",
            None,
        ),
        (
            "zenith-energy",
            "Zenith Ltd",
            "client",
            "path",
            "active",
            "shared",
            "2024-01-01",
            "2024-01-01",
            "02-Areas/Clients/Zenith-Ltd/",
            None,
        ),
        (
            "nexus-digital",
            "Nexus Digital",
            "organisation",
            "path",
            "active",
            "shared",
            "2024-01-01",
            "2024-01-01",
            "02-Areas/Work/Orgs/NexusDigital/",
            None,
        ),
        (
            "alice-chen",
            "Alice Chen",
            "person",
            "path",
            "active",
            "shared",
            "2024-01-01",
            "2024-01-01",
            "02-Areas/People/AliceChen/",
            None,
        ),
        (
            "project-atlas",
            "Project Atlas",
            "project",
            "path",
            "active",
            "shared",
            "2024-01-01",
            "2024-01-01",
            "01-Projects/Atlas/",
            None,
        ),
        (
            "triad-method",
            "Triad Method",
            "framework",
            "path",
            "active",
            "shared",
            "2024-01-01",
            "2024-01-01",
            "05-Knowledge/Frameworks/TriadMethod/",
            None,
        ),
        # Alias row — canonical_id points to acme-health
        ("acme-alias", "Acme", "client", "path", "active", "shared", "2024-01-01", "2024-01-01", None, "acme-health"),
    ]
    db.executemany(
        "INSERT INTO entities VALUES (?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    db.commit()
    db.close()
    return db_path


# ---------------------------------------------------------------------------
# load_entities_from_bootstrap
# ---------------------------------------------------------------------------


def test_bootstrap_loads_entities(bootstrap_file: str) -> None:
    """load_entities_from_bootstrap() parses at least 10 entities from the synthetic index."""
    entities = load_entities_from_bootstrap(bootstrap_file)
    assert len(entities) >= 10, f"Expected ≥10 entities, got {len(entities)}"


def test_bootstrap_entity_has_required_fields(bootstrap_file: str) -> None:
    """Each entity from bootstrap should have name, link, vault_path set."""
    entities = load_entities_from_bootstrap(bootstrap_file)
    for entity in entities:
        assert entity.name, f"Empty name: {entity}"
        assert entity.link, f"Empty link for {entity.name}"
        assert entity.vault_path, f"Empty vault_path for {entity.name}"
        assert entity.link.startswith("[["), f"Link doesn't start with [[: {entity.link}"
        assert entity.link.endswith("]]"), f"Link doesn't end with ]]: {entity.link}"


def test_bootstrap_parses_acme_health(bootstrap_file: str) -> None:
    """Acme Corp should be present with correct link and vault_path."""
    entities = load_entities_from_bootstrap(bootstrap_file)
    acme = next((e for e in entities if e.name == "Acme Corp"), None)
    assert acme is not None, "Acme Corp not found in bootstrap entities"
    assert acme.link == "[[Acme-Corp]]"
    assert "Acme-Corp" in acme.vault_path


def test_bootstrap_parses_burger_palace_alias(bootstrap_file: str) -> None:
    """Gamma Systems should parse the display-alias link form correctly."""
    entities = load_entities_from_bootstrap(bootstrap_file)
    bp = next((e for e in entities if "Gamma" in e.name), None)
    assert bp is not None, "Gamma Systems not found in bootstrap entities"
    assert bp.link == "[[Gamma-Systems|Gamma Systems]]"


def test_bootstrap_entity_types_populated(bootstrap_file: str) -> None:
    """Entity types should reflect section headings (client, organisation, person, etc.)."""
    entities = load_entities_from_bootstrap(bootstrap_file)
    types = {e.entity_type for e in entities}
    assert len(types) >= 3, f"Expected ≥3 distinct entity types, got {types}"


def test_bootstrap_handles_missing_file() -> None:
    """load_entities_from_bootstrap() returns [] for a missing file."""
    entities = load_entities_from_bootstrap("/nonexistent/path/index.md")
    assert entities == []


def test_bootstrap_no_header_rows(bootstrap_file: str) -> None:
    """Should not include rows with 'Entity' or 'Name' as the entity name."""
    entities = load_entities_from_bootstrap(bootstrap_file)
    names = [e.name for e in entities]
    assert "Entity" not in names
    assert "Name" not in names


# ---------------------------------------------------------------------------
# load_entities_from_db
# ---------------------------------------------------------------------------


def test_db_load_returns_canonicals(synthetic_db: str) -> None:
    """load_entities_from_db() returns canonical entities (canonical_id IS NULL)."""
    entities = load_entities_from_db(synthetic_db)
    names = [e.name for e in entities]
    assert "Acme Corp" in names
    assert "Zenith Ltd" in names
    assert len(entities) == 6  # 6 canonical rows, alias row excluded


def test_db_load_merges_aliases(synthetic_db: str) -> None:
    """Alias rows (canonical_id IS NOT NULL) are merged into their canonical's aliases."""
    entities = load_entities_from_db(synthetic_db)
    acme = next((e for e in entities if e.name == "Acme Corp"), None)
    assert acme is not None
    # 'Acme' alias should appear in triggers
    assert "Acme" in acme.all_triggers()


def test_db_load_skips_entities_without_vault_path(tmp_path: Path) -> None:
    """load_entities_from_db() skips entities with no vault_path."""
    db_path = str(tmp_path / "test.db")
    db = sqlite3.connect(db_path)
    db.execute(
        "CREATE TABLE entities (id TEXT PRIMARY KEY, name TEXT NOT NULL, type TEXT NOT NULL, "
        "markdown_path TEXT NOT NULL, status TEXT DEFAULT 'active', agent_scope TEXT DEFAULT 'shared', "
        "created_at TEXT, updated_at TEXT, vault_path TEXT)"
    )
    db.execute(
        "INSERT INTO entities VALUES "
        "('e1', 'Acme Corp', 'client', 'path', 'active', 'shared', "
        "'2024', '2024', '02-Areas/Clients/Acme-Corp/')"
    )
    db.execute(
        "INSERT INTO entities VALUES ('e2', 'NullPath', 'client', 'path', 'active', 'shared', '2024', '2024', NULL)"
    )
    db.commit()
    db.close()

    entities = load_entities_from_db(db_path)
    names = [e.name for e in entities]
    assert "Acme Corp" in names
    assert "NullPath" not in names


def test_db_load_returns_empty_for_missing_db() -> None:
    """load_entities_from_db() returns [] if DB file doesn't exist."""
    entities = load_entities_from_db("/nonexistent/path/entities.db")
    assert entities == []


def test_db_load_returns_empty_without_vault_path_column(tmp_path: Path) -> None:
    """load_entities_from_db() returns [] if vault_path column is missing (v1 schema)."""
    db_path = str(tmp_path / "v1.db")
    db = sqlite3.connect(db_path)
    db.execute(
        "CREATE TABLE entities (id TEXT PRIMARY KEY, name TEXT NOT NULL, "
        "type TEXT NOT NULL, markdown_path TEXT NOT NULL)"
    )
    db.execute("INSERT INTO entities VALUES ('e1', 'Acme Corp', 'client', 'path')")
    db.commit()
    db.close()

    entities = load_entities_from_db(db_path)
    assert entities == []


# ---------------------------------------------------------------------------
# get_entities: fallback logic
# ---------------------------------------------------------------------------


def test_get_entities_uses_db_when_sufficient(
    synthetic_db: str,
    bootstrap_file: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_entities() uses DB when it has >= 5 entities with vault_path."""
    import kairix.wikilinks.resolver as resolver_mod

    monkeypatch.setattr(resolver_mod, "load_entities_from_db", lambda: load_entities_from_db(synthetic_db))
    monkeypatch.setattr(
        resolver_mod, "load_entities_from_bootstrap", lambda: load_entities_from_bootstrap(bootstrap_file)
    )

    entities = get_entities(prefer_db=True)
    names = [e.name for e in entities]
    assert "Acme Corp" in names
    # Should have exactly the 6 DB canonicals, not the 12 bootstrap entries
    assert len(entities) == 6


def test_get_entities_falls_back_to_bootstrap_when_db_sparse(
    bootstrap_file: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_entities() falls back to bootstrap when DB has < 5 entities with vault_path."""
    # Sparse DB with only 2 entities
    sparse_db = str(tmp_path / "sparse.db")
    db = sqlite3.connect(sparse_db)
    db.execute(
        "CREATE TABLE entities (id TEXT PRIMARY KEY, name TEXT NOT NULL, type TEXT NOT NULL, "
        "markdown_path TEXT NOT NULL, status TEXT DEFAULT 'active', agent_scope TEXT DEFAULT 'shared', "
        "created_at TEXT, updated_at TEXT, vault_path TEXT)"
    )
    db.execute(
        "INSERT INTO entities VALUES "
        "('e1', 'Acme Corp', 'client', 'path', 'active', 'shared', "
        "'2024', '2024', '02-Areas/Clients/Acme-Corp/')"
    )
    db.execute(
        "INSERT INTO entities VALUES "
        "('e2', 'Zenith Ltd', 'client', 'path', 'active', 'shared', "
        "'2024', '2024', '02-Areas/Clients/Zenith-Ltd/')"
    )
    db.commit()
    db.close()

    import kairix.wikilinks.resolver as resolver_mod

    monkeypatch.setattr(resolver_mod, "load_entities_from_db", lambda: load_entities_from_db(sparse_db))
    monkeypatch.setattr(
        resolver_mod, "load_entities_from_bootstrap", lambda: load_entities_from_bootstrap(bootstrap_file)
    )

    entities = get_entities(prefer_db=True)
    # Should have fallen back to bootstrap (12 entries)
    assert len(entities) >= 10, f"Expected fallback to bootstrap, got {len(entities)} entities"


def test_get_entities_no_prefer_db_uses_bootstrap(
    bootstrap_file: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_entities(prefer_db=False) returns bootstrap entities directly."""
    import kairix.wikilinks.resolver as resolver_mod

    monkeypatch.setattr(
        resolver_mod, "load_entities_from_bootstrap", lambda: load_entities_from_bootstrap(bootstrap_file)
    )

    entities = get_entities(prefer_db=False)
    assert len(entities) >= 10
    names = [e.name for e in entities]
    assert "Acme Corp" in names


# ---------------------------------------------------------------------------
# WikiEntity.all_triggers
# ---------------------------------------------------------------------------


def test_wiki_entity_all_triggers_deduplicates() -> None:
    """all_triggers() should not return duplicate terms."""
    entity = WikiEntity(
        name="Acme Corp",
        aliases=["Acme Corp", "Acme", "AH"],
        vault_path="02-Areas/Clients/Acme-Corp/",
        link="[[Acme-Corp]]",
        entity_type="organisation",
    )
    triggers = entity.all_triggers()
    assert len(triggers) == len(set(triggers)), "Duplicate triggers found"
    assert "Acme Corp" in triggers
    assert "Acme" in triggers
    assert "AH" in triggers


def test_wiki_entity_all_triggers_skips_empty() -> None:
    """all_triggers() should not include empty strings."""
    entity = WikiEntity(
        name="Acme Corp",
        aliases=["", "Acme"],
        vault_path="02-Areas/Clients/Acme-Corp/",
        link="[[Acme-Corp]]",
        entity_type="organisation",
    )
    triggers = entity.all_triggers()
    assert "" not in triggers
