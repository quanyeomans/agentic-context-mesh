"""
Tests for schema v2 migration — vault_path, status, frequency, last_seen,
canonical_id columns and entity_relationships table.
"""

import sqlite3

import pytest

from kairix.entities.schema import (
    SCHEMA_VERSION,
    _migrate_v1_to_v2,
    ensure_schema,
    open_entities_db,
)


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    """Open a fresh entities DB at schema v2."""
    db_path = str(tmp_path / "entities_v2_test.db")
    monkeypatch.setenv("KAIRIX_TEST_DB", db_path)
    db = open_entities_db()
    yield db
    db.close()


@pytest.fixture()
def v1_db(tmp_path, monkeypatch):
    """Open a DB pinned to schema v1 (migration NOT run yet past v1)."""
    db_path = str(tmp_path / "entities_v1.db")
    monkeypatch.setenv("KAIRIX_TEST_DB", db_path)

    # Open and apply only the initial (v1) migration — bypass ensure_schema's auto-upgrade
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")

    from pathlib import Path

    migration = Path(__file__).parent.parent.parent / "kairix/entities/migrations/001_initial.sql"
    db.executescript(migration.read_text())

    yield db
    db.close()


# ---------------------------------------------------------------------------
# SCHEMA_VERSION constant
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_schema_version_constant_is_2() -> None:
    assert SCHEMA_VERSION == 2


# ---------------------------------------------------------------------------
# Migration v1 → v2 runs without error
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.contract
def test_migration_v1_to_v2_runs_without_error(v1_db: sqlite3.Connection) -> None:
    """_migrate_v1_to_v2 should complete without raising."""
    _migrate_v1_to_v2(v1_db)
    ver = v1_db.execute("SELECT version FROM schema_version").fetchone()[0]
    assert ver == 2


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.contract
def test_migration_v1_to_v2_is_idempotent(v1_db: sqlite3.Connection) -> None:
    """Running _migrate_v1_to_v2 twice must not raise and schema_version stays at 2."""
    _migrate_v1_to_v2(v1_db)
    _migrate_v1_to_v2(v1_db)  # second call
    ver = v1_db.execute("SELECT version FROM schema_version").fetchone()[0]
    assert ver == 2


@pytest.mark.unit
@pytest.mark.contract
def test_ensure_schema_idempotent_on_v2(fresh_db: sqlite3.Connection) -> None:
    """ensure_schema on an already-v2 DB must be a no-op."""
    ensure_schema(fresh_db)
    ver = fresh_db.execute("SELECT version FROM schema_version").fetchone()[0]
    assert ver == 2


# ---------------------------------------------------------------------------
# entity_relationships table
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.contract
def test_entity_relationships_table_exists(fresh_db: sqlite3.Connection) -> None:
    """entity_relationships table must exist after v2 migration."""
    tables = {row[0] for row in fresh_db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "entity_relationships" in tables


@pytest.mark.unit
@pytest.mark.contract
def test_entity_relationships_indexes_exist(fresh_db: sqlite3.Connection) -> None:
    """Indexes on entity_relationships must be created."""
    indexes = {row[0] for row in fresh_db.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    assert "idx_relationships_a" in indexes
    assert "idx_relationships_b" in indexes


# ---------------------------------------------------------------------------
# New columns on entities
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.contract
def test_entities_has_vault_path_column(fresh_db: sqlite3.Connection) -> None:
    columns = {row[1] for row in fresh_db.execute("PRAGMA table_info(entities)").fetchall()}
    assert "vault_path" in columns


@pytest.mark.unit
@pytest.mark.contract
def test_entities_has_status_column(fresh_db: sqlite3.Connection) -> None:
    columns = {row[1] for row in fresh_db.execute("PRAGMA table_info(entities)").fetchall()}
    assert "status" in columns


@pytest.mark.unit
@pytest.mark.contract
def test_entities_has_frequency_column(fresh_db: sqlite3.Connection) -> None:
    columns = {row[1] for row in fresh_db.execute("PRAGMA table_info(entities)").fetchall()}
    assert "frequency" in columns


@pytest.mark.unit
@pytest.mark.contract
def test_entities_has_last_seen_column(fresh_db: sqlite3.Connection) -> None:
    columns = {row[1] for row in fresh_db.execute("PRAGMA table_info(entities)").fetchall()}
    assert "last_seen" in columns


@pytest.mark.unit
@pytest.mark.contract
def test_entities_has_canonical_id_column(fresh_db: sqlite3.Connection) -> None:
    columns = {row[1] for row in fresh_db.execute("PRAGMA table_info(entities)").fetchall()}
    assert "canonical_id" in columns


# ---------------------------------------------------------------------------
# Default values on new columns
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.contract
def test_frequency_defaults_to_zero(fresh_db: sqlite3.Connection) -> None:
    """New entities should have frequency=0 by default."""
    now = "2026-01-01T00:00:00Z"
    fresh_db.execute(
        "INSERT INTO entities (id, type, name, markdown_path, created_at, updated_at)"
        " VALUES ('test-e', 'person', 'Test Entity', 'test.md', ?, ?)",
        (now, now),
    )
    fresh_db.commit()
    row = fresh_db.execute("SELECT frequency FROM entities WHERE id='test-e'").fetchone()
    assert row[0] == 0


@pytest.mark.unit
@pytest.mark.contract
def test_vault_path_defaults_to_null(fresh_db: sqlite3.Connection) -> None:
    """New entities should have vault_path=NULL by default."""
    now = "2026-01-01T00:00:00Z"
    fresh_db.execute(
        "INSERT INTO entities (id, type, name, markdown_path, created_at, updated_at)"
        " VALUES ('test-vp', 'person', 'VP Entity', 'test.md', ?, ?)",
        (now, now),
    )
    fresh_db.commit()
    row = fresh_db.execute("SELECT vault_path FROM entities WHERE id='test-vp'").fetchone()
    assert row[0] is None
