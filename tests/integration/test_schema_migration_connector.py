"""
SC-4 integration tests: connector-framework schema migration.

Covers ADR-018 storage-tiering schema additions (Wave 1):

  * Fresh ``create_schema()`` produces all new connector-framework tables.
  * Legacy v1 schema (no connector columns / tables) is migrated additively
    by ``migrate()`` — existing rows survive untouched.
  * ``migrate()`` is idempotent — running it twice does not double-add
    columns and does not error.
  * Existing ``documents`` rows keep their original values, gain
    ``sensitivity='public'`` by default, and ``NULL`` for the other new
    columns.

These tests intentionally hand-build the v1 schema rather than calling
``create_schema()`` so that the migration path is exercised end-to-end.
"""

from __future__ import annotations

import sqlite3

import pytest

from kairix.core.db.schema import (
    SCHEMA_VERSION,
    create_schema,
    migrate,
    validate_schema,
)

# Names of the six new tables added in SCHEMA_VERSION 2 (SC-4).
NEW_CONNECTOR_TABLES: tuple[str, ...] = (
    "documents_media",
    "document_pages",
    "connector_cursors",
    "connector_deadletter",
    "bronze_records",
    "entity_signals",
)

# Names of the five new columns added to ``documents`` in SCHEMA_VERSION 2.
NEW_DOCUMENT_COLUMNS: tuple[str, ...] = (
    "source_name",
    "source_uri",
    "source_modified_at",
    "source_page",
    "sensitivity",
)


def _build_legacy_v1_db(db: sqlite3.Connection) -> None:
    """
    Build a pre-SC-4 schema by hand and seed one documents row.

    This is the on-disk shape a dogfood DB created under SCHEMA_VERSION 1
    would have — no source_* columns, no sensitivity column, none of the
    new connector tables.
    """
    db.executescript(
        """
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collection TEXT NOT NULL,
            path TEXT NOT NULL,
            title TEXT,
            hash TEXT NOT NULL,
            created_at TEXT,
            modified_at TEXT,
            active INTEGER DEFAULT 1,
            agent_owner TEXT,
            UNIQUE(collection, path)
        );
        CREATE TABLE content (hash TEXT PRIMARY KEY, doc TEXT, created_at TEXT);
        CREATE TABLE content_vectors (
            hash TEXT NOT NULL,
            seq INTEGER NOT NULL,
            pos INTEGER NOT NULL,
            model TEXT,
            embedded_at TEXT,
            chunk_date TEXT,
            PRIMARY KEY (hash, seq)
        );
        CREATE TABLE kairix_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO documents (collection, path, title, hash, active, agent_owner)
        VALUES ('areas', '02-Areas/notes.md', 'Pre-SC4 Doc', 'h0', 1, 'agent-alpha');
        INSERT INTO kairix_meta (key, value) VALUES ('schema_version', '1');
        """
    )
    db.commit()


@pytest.mark.integration
def test_fresh_create_schema_includes_all_connector_tables() -> None:
    """create_schema() on a brand-new DB produces every Wave 1 table."""
    db = sqlite3.connect(":memory:")
    create_schema(db)

    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for required in NEW_CONNECTOR_TABLES:
        assert required in tables, f"fresh schema missing table: {required}"


@pytest.mark.integration
def test_fresh_create_schema_includes_all_connector_columns_on_documents() -> None:
    """Fresh documents table has every Wave 1 connector-framework column."""
    db = sqlite3.connect(":memory:")
    create_schema(db)

    cols = {row[1] for row in db.execute("PRAGMA table_info(documents)")}
    for required in NEW_DOCUMENT_COLUMNS:
        assert required in cols, f"fresh documents table missing column: {required}"


@pytest.mark.integration
def test_fresh_create_schema_creates_source_uri_index() -> None:
    """create_schema() creates the idx_documents_source_uri lookup index."""
    db = sqlite3.connect(":memory:")
    create_schema(db)

    indexes = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_documents_source_uri" in indexes


@pytest.mark.integration
def test_fresh_create_schema_bumps_schema_version_meta_row() -> None:
    """kairix_meta.schema_version reflects the bumped SCHEMA_VERSION."""
    db = sqlite3.connect(":memory:")
    create_schema(db)

    row = db.execute("SELECT value FROM kairix_meta WHERE key='schema_version'").fetchone()
    assert row is not None
    assert row[0] == SCHEMA_VERSION
    # Explicit: the bump landed (was "1" pre-SC-4).
    assert SCHEMA_VERSION == "2"


@pytest.mark.integration
def test_migrate_legacy_v1_db_adds_all_new_tables() -> None:
    """Running migrate() against a v1 DB lands every Wave 1 table."""
    db = sqlite3.connect(":memory:")
    _build_legacy_v1_db(db)

    migrate(db)

    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for required in NEW_CONNECTOR_TABLES:
        assert required in tables, f"legacy migration missing table: {required}"


@pytest.mark.integration
def test_migrate_legacy_v1_db_adds_all_new_documents_columns() -> None:
    """Running migrate() against a v1 DB lands every Wave 1 documents column."""
    db = sqlite3.connect(":memory:")
    _build_legacy_v1_db(db)

    migrate(db)

    cols = {row[1] for row in db.execute("PRAGMA table_info(documents)")}
    for required in NEW_DOCUMENT_COLUMNS:
        assert required in cols, f"legacy migration missing column: {required}"


@pytest.mark.integration
def test_migrate_legacy_v1_db_preserves_existing_documents_row() -> None:
    """Existing documents rows survive migration with original values intact.

    The new ``sensitivity`` column defaults to ``'public'`` per the DDL
    default; the four other new columns are NULL for legacy rows.
    """
    db = sqlite3.connect(":memory:")
    _build_legacy_v1_db(db)

    migrate(db)

    row = db.execute(
        "SELECT collection, path, title, hash, agent_owner, "
        "       source_name, source_uri, source_modified_at, source_page, sensitivity "
        "FROM documents"
    ).fetchone()
    assert row is not None
    (
        collection,
        path,
        title,
        digest,
        agent_owner,
        source_name,
        source_uri,
        source_modified_at,
        source_page,
        sensitivity,
    ) = row

    # Original values preserved
    assert collection == "areas"
    assert path == "02-Areas/notes.md"
    assert title == "Pre-SC4 Doc"
    assert digest == "h0"
    assert agent_owner == "agent-alpha"

    # New nullable columns default to NULL on legacy rows
    assert source_name is None
    assert source_uri is None
    assert source_modified_at is None
    assert source_page is None

    # sensitivity has DDL default 'public'
    assert sensitivity == "public"


@pytest.mark.integration
def test_migrate_is_idempotent_on_already_migrated_db() -> None:
    """Running migrate() twice does not double-add columns or error."""
    db = sqlite3.connect(":memory:")
    _build_legacy_v1_db(db)

    migrate(db)
    # Snapshot column set after first migration
    cols_first = {row[1] for row in db.execute("PRAGMA table_info(documents)")}

    # Second call must be a no-op (no exception)
    migrate(db)
    cols_second = {row[1] for row in db.execute("PRAGMA table_info(documents)")}

    assert cols_first == cols_second
    for required in NEW_DOCUMENT_COLUMNS:
        assert required in cols_second


@pytest.mark.integration
def test_migrate_is_idempotent_on_fresh_db() -> None:
    """create_schema() then migrate() then migrate() is safe (no-op)."""
    db = sqlite3.connect(":memory:")
    create_schema(db)
    # create_schema already calls migrate() internally; calling it again
    # must not raise and must not change the table/column set.
    tables_before = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    cols_before = {row[1] for row in db.execute("PRAGMA table_info(documents)")}

    migrate(db)
    migrate(db)

    tables_after = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    cols_after = {row[1] for row in db.execute("PRAGMA table_info(documents)")}

    assert tables_before == tables_after
    assert cols_before == cols_after


@pytest.mark.integration
def test_migrate_then_validate_schema_returns_clean() -> None:
    """After migrating a legacy v1 DB, validate_schema reports no errors."""
    db = sqlite3.connect(":memory:")
    _build_legacy_v1_db(db)

    migrate(db)

    errors = validate_schema(db)
    assert errors == [], f"unexpected schema errors after migration: {errors}"


@pytest.mark.integration
def test_legacy_db_uses_create_schema_path_end_to_end() -> None:
    """create_schema() on a v1 DB lands all new tables, columns, and indexes."""
    db = sqlite3.connect(":memory:")
    _build_legacy_v1_db(db)

    # create_schema must be safe to run on a partial / legacy schema
    create_schema(db)

    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    cols = {row[1] for row in db.execute("PRAGMA table_info(documents)")}
    indexes = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='index'")}

    for required in NEW_CONNECTOR_TABLES:
        assert required in tables
    for required in NEW_DOCUMENT_COLUMNS:
        assert required in cols
    assert "idx_documents_source_uri" in indexes

    # Schema version row updated to current
    row = db.execute("SELECT value FROM kairix_meta WHERE key='schema_version'").fetchone()
    assert row is not None
    assert row[0] == SCHEMA_VERSION


@pytest.mark.integration
def test_new_connector_tables_have_expected_primary_keys() -> None:
    """Wave 1 tables enforce the documented primary-key shapes.

    Locks the cursor/deadletter/bronze invariants — duplicate (source_name,
    item_id) records must be rejected.
    """
    db = sqlite3.connect(":memory:")
    create_schema(db)

    # connector_cursors: source_name is PK
    db.execute(
        "INSERT INTO connector_cursors (source_name, cursor_token, updated_at) "
        "VALUES ('obsidian', 't0', '2026-05-22T00:00:00Z')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO connector_cursors (source_name, cursor_token, updated_at) "
            "VALUES ('obsidian', 't1', '2026-05-22T00:00:01Z')"
        )
    db.rollback()

    # bronze_records: composite PK (source_name, item_id)
    db.execute(
        "INSERT INTO bronze_records (source_name, item_id, raw_path, mime, fetched_at) "
        "VALUES ('obsidian', 'i1', 'obsidian/h1', 'text/markdown', '2026-05-22T00:00:00Z')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO bronze_records (source_name, item_id, raw_path, mime, fetched_at) "
            "VALUES ('obsidian', 'i1', 'obsidian/h2', 'text/markdown', '2026-05-22T00:00:05Z')"
        )
    db.rollback()

    # connector_deadletter: UNIQUE (source_name, item_id)
    db.execute(
        "INSERT INTO connector_deadletter (source_name, item_id, failure_count, last_attempt) "
        "VALUES ('obsidian', 'bad-1', 1, '2026-05-22T00:00:00Z')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO connector_deadletter (source_name, item_id, failure_count, last_attempt) "
            "VALUES ('obsidian', 'bad-1', 2, '2026-05-22T00:00:10Z')"
        )
    db.rollback()
