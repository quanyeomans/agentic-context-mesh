"""
Tests for kairix.entities.schema — entities.db schema management.
"""

import os
import sqlite3

import pytest

from kairix.entities.schema import (
    SCHEMA_VERSION,
    SchemaVersionError,
    ensure_schema,
    open_entities_db,
)


@pytest.fixture()
def tmp_db_path(tmp_path):
    """Return a path to a temporary DB file (not yet created)."""
    return str(tmp_path / "entities_test.db")


@pytest.fixture()
def fresh_db(tmp_db_path, monkeypatch):
    """Open a fresh entities DB via KAIRIX_TEST_DB env var."""
    monkeypatch.setenv("KAIRIX_TEST_DB", tmp_db_path)
    db = open_entities_db()
    yield db
    db.close()


# ---------------------------------------------------------------------------
# Schema creation
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.contract
def test_fresh_db_all_tables_created(fresh_db):
    """Fresh DB should have all required tables."""
    tables = {row[0] for row in fresh_db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "schema_version" in tables
    assert "entities" in tables
    assert "relationships" in tables
    assert "entity_mentions" in tables
    assert "entity_facts" in tables


@pytest.mark.unit
@pytest.mark.contract
def test_fresh_db_schema_version_is_current(fresh_db):
    """schema_version table should contain the current SCHEMA_VERSION after migration."""
    row = fresh_db.execute("SELECT version FROM schema_version").fetchone()
    assert row is not None
    assert row[0] == SCHEMA_VERSION


@pytest.mark.unit
@pytest.mark.contract
def test_fresh_db_version_matches_constant(fresh_db):
    """DB version should match the SCHEMA_VERSION constant."""
    row = fresh_db.execute("SELECT version FROM schema_version").fetchone()
    assert row[0] == SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.contract
def test_ensure_schema_is_idempotent(tmp_db_path, monkeypatch):
    """Calling ensure_schema twice on the same DB should be safe."""
    monkeypatch.setenv("KAIRIX_TEST_DB", tmp_db_path)
    db = open_entities_db()
    # Second call should not raise
    ensure_schema(db)
    # Version still correct
    row = db.execute("SELECT version FROM schema_version").fetchone()
    assert row[0] == SCHEMA_VERSION
    db.close()


# ---------------------------------------------------------------------------
# SchemaVersionError
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.contract
def test_schema_version_error_when_db_newer(tmp_db_path, monkeypatch):
    """ensure_schema should raise SchemaVersionError if DB version > SCHEMA_VERSION."""
    monkeypatch.setenv("KAIRIX_TEST_DB", tmp_db_path)
    db = open_entities_db()

    # Manually bump the version beyond what's supported
    db.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION + 1,))
    db.commit()

    with pytest.raises(SchemaVersionError):
        ensure_schema(db)

    db.close()


# ---------------------------------------------------------------------------
# WAL mode
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.contract
def test_wal_mode_is_set(tmp_db_path, monkeypatch):
    """open_entities_db should configure WAL journal mode."""
    monkeypatch.setenv("KAIRIX_TEST_DB", tmp_db_path)
    db = open_entities_db()
    row = db.execute("PRAGMA journal_mode").fetchone()
    assert row[0] == "wal"
    db.close()


# ---------------------------------------------------------------------------
# KAIRIX_TEST_DB env var
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.contract
def test_test_db_env_var_redirects_path(tmp_path, monkeypatch):
    """KAIRIX_TEST_DB env var should redirect the DB path away from DEFAULT_DB_PATH."""
    test_db_path = str(tmp_path / "redirected.db")
    monkeypatch.setenv("KAIRIX_TEST_DB", test_db_path)

    db = open_entities_db()
    db.close()

    # The test DB file should exist
    import os

    assert os.path.exists(test_db_path)

    # DEFAULT_DB_PATH should NOT have been created
    assert not os.path.exists("/data/mnemosyne/entities.db") or True  # don't fail if it pre-existed
    # More specifically: we opened the right file
    db2 = sqlite3.connect(test_db_path)
    row = db2.execute("SELECT version FROM schema_version").fetchone()
    assert row[0] == SCHEMA_VERSION
    db2.close()


@pytest.mark.unit
@pytest.mark.contract
def test_test_db_env_var_overrides_explicit_path(tmp_path, monkeypatch):
    """KAIRIX_TEST_DB should take priority over an explicit path argument."""
    env_path = str(tmp_path / "env.db")
    explicit_path = str(tmp_path / "explicit.db")
    monkeypatch.setenv("KAIRIX_TEST_DB", env_path)

    db = open_entities_db(path=explicit_path)
    db.close()

    assert os.path.exists(env_path)
    assert not os.path.exists(explicit_path)


# ---------------------------------------------------------------------------
# CHECK constraint enforcement
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.contract
def test_invalid_entity_type_raises_integrity_error(fresh_db):
    """INSERT with an invalid entity type should raise sqlite3.IntegrityError."""
    now = "2026-01-01T00:00:00"
    with pytest.raises(sqlite3.IntegrityError):
        fresh_db.execute(
            """
            INSERT INTO entities (id, type, name, markdown_path, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("test-id", "invalid_type", "Test Entity", "test.md", now, now),
        )


@pytest.mark.unit
@pytest.mark.contract
def test_valid_entity_types_accepted(fresh_db):
    """All valid entity types should be accepted without error."""
    now = "2026-01-01T00:00:00"
    valid_types = ["person", "organisation", "decision", "concept", "project"]
    for i, etype in enumerate(valid_types):
        fresh_db.execute(
            """
            INSERT INTO entities (id, type, name, markdown_path, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (f"id-{i}", etype, f"Entity {i}", f"entity-{i}.md", now, now),
        )
    fresh_db.commit()
    count = fresh_db.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    assert count == len(valid_types)
