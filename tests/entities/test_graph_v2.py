"""
Tests for schema v2 graph functions:
  set_vault_path, get_by_vault_path, update_frequency,
  write_relationship, get_relationships, set_canonical, get_canonical
"""

import pytest

from kairix.entities.graph import (
    entity_write,
    get_by_vault_path,
    get_canonical,
    get_relationships,
    set_canonical,
    set_vault_path,
    update_frequency,
    write_relationship,
)
from kairix.entities.schema import open_entities_db

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Open a fresh v2 entities DB backed by a temp file."""
    db_path = str(tmp_path / "test_v2.db")
    monkeypatch.setenv("KAIRIX_TEST_DB", db_path)
    conn = open_entities_db()
    yield conn
    conn.close()


@pytest.fixture()
def vault_root(tmp_path, monkeypatch):
    """Patch VAULT_ROOT to avoid writing to /data/obsidian-vault."""
    vault = tmp_path / "vault"
    vault.mkdir()
    import kairix.entities.graph as graph_module

    monkeypatch.setattr(graph_module, "VAULT_ROOT", vault)
    return vault


@pytest.fixture()
def entity_dan(db, vault_root):
    """Create 'Alice Chen' entity and return its id."""
    return entity_write(
        name="Alice Chen",
        entity_type="person",
        markdown_path="06-Entities/person/alice-chen.md",
        db=db,
    )


@pytest.fixture()
def entity_tc(db, vault_root):
    """Create 'Acme Corp' entity and return its id."""
    return entity_write(
        name="Acme Corp",
        entity_type="organisation",
        markdown_path="06-Entities/organisation/acme-corp.md",
        db=db,
    )


# ---------------------------------------------------------------------------
# set_vault_path + get_by_vault_path — round-trip
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_set_and_get_vault_path_round_trip(db, entity_dan) -> None:
    """set_vault_path then get_by_vault_path should return the same entity."""
    set_vault_path(entity_dan, "02-Areas/Career/", db)
    result = get_by_vault_path("02-Areas/Career/", db)
    assert result is not None
    assert result["id"] == entity_dan
    assert result["vault_path"] == "02-Areas/Career/"


@pytest.mark.unit
def test_get_by_vault_path_not_found_returns_none(db) -> None:
    """get_by_vault_path should return None for unknown path."""
    result = get_by_vault_path("99-NonExistent/", db)
    assert result is None


@pytest.mark.unit
def test_set_vault_path_overwrites(db, entity_dan) -> None:
    """set_vault_path called twice should update the value."""
    set_vault_path(entity_dan, "02-Areas/Career/", db)
    set_vault_path(entity_dan, "02-Areas/Career/Updated/", db)
    result = get_by_vault_path("02-Areas/Career/Updated/", db)
    assert result is not None
    assert result["id"] == entity_dan


# ---------------------------------------------------------------------------
# update_frequency
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_update_frequency_increments_count(db, entity_dan) -> None:
    """update_frequency should increment the frequency column."""
    update_frequency(entity_dan, db)
    row = db.execute("SELECT frequency FROM entities WHERE id = ?", (entity_dan,)).fetchone()
    assert row[0] == 1

    update_frequency(entity_dan, db)
    row = db.execute("SELECT frequency FROM entities WHERE id = ?", (entity_dan,)).fetchone()
    assert row[0] == 2


@pytest.mark.unit
def test_update_frequency_sets_last_seen(db, entity_dan) -> None:
    """update_frequency should set last_seen to today (YYYY-MM-DD)."""
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    update_frequency(entity_dan, db)
    row = db.execute("SELECT last_seen FROM entities WHERE id = ?", (entity_dan,)).fetchone()
    assert row[0] == today


# ---------------------------------------------------------------------------
# write_relationship — upsert (no duplicate on re-run)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_write_relationship_creates_row(db, entity_dan, entity_tc) -> None:
    """write_relationship should insert a row into entity_relationships."""
    write_relationship(entity_dan, entity_tc, "co_occurs", 0.8, db)
    rows = db.execute("SELECT * FROM entity_relationships").fetchall()
    assert len(rows) == 1
    row = dict(rows[0])
    assert row["entity_a_id"] == entity_dan
    assert row["entity_b_id"] == entity_tc
    assert row["relationship_type"] == "co_occurs"
    assert abs(row["strength"] - 0.8) < 1e-9


@pytest.mark.unit
def test_write_relationship_upsert_no_duplicate(db, entity_dan, entity_tc) -> None:
    """Calling write_relationship twice with same key should NOT create a duplicate row."""
    write_relationship(entity_dan, entity_tc, "co_occurs", 0.8, db)
    write_relationship(entity_dan, entity_tc, "co_occurs", 0.9, db)
    count = db.execute("SELECT COUNT(*) FROM entity_relationships").fetchone()[0]
    assert count == 1
    row = db.execute("SELECT strength FROM entity_relationships").fetchone()
    assert abs(row[0] - 0.9) < 1e-9  # updated strength


@pytest.mark.unit
def test_write_relationship_different_type_is_separate(db, entity_dan, entity_tc) -> None:
    """Different relationship_type between same pair creates separate rows."""
    write_relationship(entity_dan, entity_tc, "co_occurs", 0.8, db)
    write_relationship(entity_dan, entity_tc, "part_of", 0.5, db)
    count = db.execute("SELECT COUNT(*) FROM entity_relationships").fetchone()[0]
    assert count == 2


# ---------------------------------------------------------------------------
# get_relationships — both directions
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_get_relationships_returns_both_directions(db, entity_dan, entity_tc, vault_root) -> None:
    """get_relationships should return rows where entity is a or b side."""
    # Dan → Acme Corp
    write_relationship(entity_dan, entity_tc, "co_occurs", 0.7, db)

    # Another entity → Dan
    other_id = entity_write(
        name="Other Entity",
        entity_type="concept",
        markdown_path="06-Entities/concept/other-entity.md",
        db=db,
    )
    write_relationship(other_id, entity_dan, "related_to", 0.5, db)

    rels = get_relationships(entity_dan, db)
    assert len(rels) == 2
    pairs = {(r["entity_a_id"], r["entity_b_id"]) for r in rels}
    assert (entity_dan, entity_tc) in pairs
    assert (other_id, entity_dan) in pairs


@pytest.mark.unit
def test_get_relationships_empty_when_none(db, entity_dan) -> None:
    """get_relationships returns empty list when entity has no relationships."""
    rels = get_relationships(entity_dan, db)
    assert rels == []


# ---------------------------------------------------------------------------
# set_canonical + get_canonical — alias chain
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_set_and_get_canonical_resolves_alias(db, entity_dan, entity_tc, vault_root) -> None:
    """set_canonical + get_canonical should resolve alias → canonical."""
    # Create an alias entity
    alias_id = entity_write(
        name="TC",
        entity_type="organisation",
        markdown_path="06-Entities/organisation/tc.md",
        db=db,
    )
    set_canonical(alias_id, entity_tc, db)
    result = get_canonical(alias_id, db)
    assert result is not None
    assert result["id"] == entity_tc


@pytest.mark.unit
def test_get_canonical_self_when_no_alias(db, entity_dan) -> None:
    """get_canonical on a canonical entity should return itself."""
    result = get_canonical(entity_dan, db)
    assert result is not None
    assert result["id"] == entity_dan
    assert result["canonical_id"] is None


@pytest.mark.unit
def test_get_canonical_not_found_returns_none(db) -> None:
    """get_canonical for a non-existent entity should return None."""
    result = get_canonical("nonexistent-id", db)
    assert result is None
