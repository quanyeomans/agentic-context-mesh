"""Tests for kairix.db.schema — schema creation, validation, migration."""

import sqlite3

import pytest

from kairix.db.schema import create_schema, migrate, validate_schema


@pytest.mark.unit
def test_create_schema_creates_all_tables() -> None:
    """create_schema() creates documents, content, content_vectors, kairix_meta, documents_fts."""
    db = sqlite3.connect(":memory:")
    # sqlite-vec not available in unit tests — skip vectors_vec
    # We test the non-vec parts of schema creation
    try:
        import sqlite_vec

        db.enable_load_extension(True)
        db.load_extension(sqlite_vec.loadable_path())
        db.enable_load_extension(False)
        has_vec = True
    except (ImportError, AttributeError):
        has_vec = False

    if has_vec:
        create_schema(db)
    else:
        # Create schema without vec table
        db.executescript("""
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collection TEXT NOT NULL,
                path TEXT NOT NULL,
                title TEXT,
                hash TEXT NOT NULL,
                created_at TEXT,
                modified_at TEXT,
                active INTEGER DEFAULT 1,
                UNIQUE(collection, path)
            );
            CREATE TABLE IF NOT EXISTS content (
                hash TEXT PRIMARY KEY,
                doc TEXT,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS content_vectors (
                hash TEXT NOT NULL,
                seq INTEGER NOT NULL,
                pos INTEGER NOT NULL,
                model TEXT,
                embedded_at TEXT,
                chunk_date TEXT,
                PRIMARY KEY (hash, seq)
            );
            CREATE TABLE IF NOT EXISTS kairix_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)

    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "documents" in tables
    assert "content" in tables
    assert "content_vectors" in tables
    assert "kairix_meta" in tables


@pytest.mark.unit
def test_validate_schema_passes_on_valid_db() -> None:
    """validate_schema returns empty list on a correctly structured DB."""
    db = sqlite3.connect(":memory:")
    db.executescript("""
        CREATE TABLE documents (id INTEGER PRIMARY KEY, collection TEXT, path TEXT, hash TEXT, active INTEGER);
        CREATE TABLE content (hash TEXT PRIMARY KEY, doc TEXT);
        CREATE TABLE content_vectors (hash TEXT, seq INTEGER, pos INTEGER, PRIMARY KEY(hash, seq));
    """)
    errors = validate_schema(db)
    assert errors == []


@pytest.mark.unit
def test_validate_schema_detects_missing_table() -> None:
    """validate_schema reports missing tables."""
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, collection TEXT, path TEXT, hash TEXT, active INTEGER)")
    errors = validate_schema(db)
    assert any("content" in e for e in errors)


@pytest.mark.unit
def test_validate_schema_detects_missing_column() -> None:
    """validate_schema reports missing columns."""
    db = sqlite3.connect(":memory:")
    db.executescript("""
        CREATE TABLE documents (id INTEGER PRIMARY KEY, collection TEXT, path TEXT, active INTEGER);
        CREATE TABLE content (hash TEXT PRIMARY KEY, doc TEXT);
        CREATE TABLE content_vectors (hash TEXT, seq INTEGER, pos INTEGER, PRIMARY KEY(hash, seq));
    """)
    errors = validate_schema(db)
    assert any("hash" in e for e in errors)


@pytest.mark.unit
def test_migrate_adds_chunk_date() -> None:
    """migrate() adds chunk_date column to content_vectors if missing."""
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE content_vectors (hash TEXT, seq INTEGER, pos INTEGER, PRIMARY KEY(hash, seq))")
    migrate(db)
    cols = {row[1] for row in db.execute("PRAGMA table_info(content_vectors)")}
    assert "chunk_date" in cols


@pytest.mark.unit
def test_migrate_idempotent() -> None:
    """migrate() is safe to call repeatedly."""
    db = sqlite3.connect(":memory:")
    db.execute(
        "CREATE TABLE content_vectors (hash TEXT, seq INTEGER, pos INTEGER, chunk_date TEXT, PRIMARY KEY(hash, seq))"
    )
    # Should not raise
    migrate(db)
    migrate(db)
    cols = {row[1] for row in db.execute("PRAGMA table_info(content_vectors)")}
    assert "chunk_date" in cols
