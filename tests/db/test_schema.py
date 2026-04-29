"""Tests for kairix.core.db.schema — schema creation, validation, migration."""

import sqlite3
from unittest.mock import patch

import pytest

from kairix.core.db.schema import SCHEMA_VERSION, _ensure_vec_table, create_schema, migrate, validate_schema


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


@pytest.mark.unit
def test_validate_schema_empty_db_reports_all_missing() -> None:
    """validate_schema on completely empty DB reports all required tables missing."""
    db = sqlite3.connect(":memory:")
    errors = validate_schema(db)
    assert len(errors) == 3
    assert any("documents" in e for e in errors)
    assert any("content" in e for e in errors)
    assert any("content_vectors" in e for e in errors)


@pytest.mark.unit
def test_validate_schema_missing_content_vectors_only() -> None:
    """validate_schema reports just the missing table."""
    db = sqlite3.connect(":memory:")
    db.executescript("""
        CREATE TABLE documents (id INTEGER PRIMARY KEY, collection TEXT, path TEXT, hash TEXT, active INTEGER);
        CREATE TABLE content (hash TEXT PRIMARY KEY, doc TEXT);
    """)
    errors = validate_schema(db)
    assert len(errors) == 1
    assert "content_vectors" in errors[0]


@pytest.mark.unit
def test_create_schema_creates_fts_table() -> None:
    """create_schema creates the documents_fts FTS5 virtual table."""
    db = sqlite3.connect(":memory:")
    try:
        import sqlite_vec

        db.enable_load_extension(True)
        db.load_extension(sqlite_vec.loadable_path())
        db.enable_load_extension(False)
    except (ImportError, AttributeError):
        pytest.skip("sqlite-vec not available")

    create_schema(db)
    fts = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='documents_fts'").fetchone()
    assert fts is not None


@pytest.mark.unit
def test_create_schema_sets_schema_version() -> None:
    """create_schema stores the schema version in kairix_meta."""
    db = sqlite3.connect(":memory:")
    try:
        import sqlite_vec

        db.enable_load_extension(True)
        db.load_extension(sqlite_vec.loadable_path())
        db.enable_load_extension(False)
    except (ImportError, AttributeError):
        pytest.skip("sqlite-vec not available")

    create_schema(db)
    row = db.execute("SELECT value FROM kairix_meta WHERE key='schema_version'").fetchone()
    assert row is not None
    assert row[0] == "1"


@pytest.mark.unit
def test_create_schema_idempotent() -> None:
    """create_schema can be called twice without error."""
    db = sqlite3.connect(":memory:")
    try:
        import sqlite_vec

        db.enable_load_extension(True)
        db.load_extension(sqlite_vec.loadable_path())
        db.enable_load_extension(False)
    except (ImportError, AttributeError):
        pytest.skip("sqlite-vec not available")

    create_schema(db)
    create_schema(db)  # second call should not raise
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "documents" in tables


@pytest.mark.unit
def test_migrate_creates_kairix_meta() -> None:
    """migrate() creates kairix_meta table if it does not exist."""
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE content_vectors (hash TEXT, seq INTEGER, pos INTEGER, PRIMARY KEY(hash, seq))")
    migrate(db)
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "kairix_meta" in tables


@pytest.mark.unit
def test_migrate_creates_indexes_on_documents() -> None:
    """migrate() creates indexes on existing documents table."""
    db = sqlite3.connect(":memory:")
    db.executescript("""
        CREATE TABLE documents (id INTEGER PRIMARY KEY, collection TEXT, path TEXT, hash TEXT, active INTEGER);
        CREATE TABLE content_vectors (hash TEXT, seq INTEGER, pos INTEGER, chunk_date TEXT, PRIMARY KEY(hash, seq));
    """)
    migrate(db)
    indexes = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_documents_hash" in indexes
    assert "idx_documents_collection" in indexes
    assert "idx_documents_active" in indexes


@pytest.mark.unit
def test_migrate_creates_chunk_date_index() -> None:
    """migrate() creates idx_content_vectors_chunk_date index."""
    db = sqlite3.connect(":memory:")
    db.execute(
        "CREATE TABLE content_vectors (hash TEXT, seq INTEGER, pos INTEGER, chunk_date TEXT, PRIMARY KEY(hash, seq))"
    )
    migrate(db)
    indexes = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_content_vectors_chunk_date" in indexes


@pytest.mark.unit
def test_create_schema_with_mocked_vec() -> None:
    """create_schema creates all tables when _ensure_vec_table is mocked."""
    db = sqlite3.connect(":memory:")
    with patch("kairix.core.db.schema._ensure_vec_table"):
        create_schema(db)

    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "documents" in tables
    assert "content" in tables
    assert "content_vectors" in tables
    assert "kairix_meta" in tables
    assert "documents_fts" in tables

    # Check schema version
    row = db.execute("SELECT value FROM kairix_meta WHERE key='schema_version'").fetchone()
    assert row[0] == SCHEMA_VERSION

    # Check created_by
    row = db.execute("SELECT value FROM kairix_meta WHERE key='created_by'").fetchone()
    assert row[0] == "kairix"


@pytest.mark.unit
def test_create_schema_idempotent_with_mock() -> None:
    """create_schema can be called twice without error (mocked vec)."""
    db = sqlite3.connect(":memory:")
    with patch("kairix.core.db.schema._ensure_vec_table"):
        create_schema(db)
        create_schema(db)  # second call should not raise

    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "documents" in tables


@pytest.mark.unit
def test_create_schema_creates_indexes_with_mock() -> None:
    """create_schema creates expected indexes."""
    db = sqlite3.connect(":memory:")
    with patch("kairix.core.db.schema._ensure_vec_table"):
        create_schema(db)

    indexes = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_documents_hash" in indexes
    assert "idx_documents_collection" in indexes
    assert "idx_documents_active" in indexes
    assert "idx_content_vectors_chunk_date" in indexes


@pytest.mark.unit
def test_create_schema_documents_table_columns_with_mock() -> None:
    """create_schema creates documents table with all expected columns."""
    db = sqlite3.connect(":memory:")
    with patch("kairix.core.db.schema._ensure_vec_table"):
        create_schema(db)

    cols = {row[1] for row in db.execute("PRAGMA table_info(documents)")}
    expected = {"id", "collection", "path", "title", "hash", "created_at", "modified_at", "active"}
    assert expected.issubset(cols)


@pytest.mark.unit
def test_create_schema_content_vectors_has_chunk_date_with_mock() -> None:
    """create_schema creates content_vectors with chunk_date column."""
    db = sqlite3.connect(":memory:")
    with patch("kairix.core.db.schema._ensure_vec_table"):
        create_schema(db)

    cols = {row[1] for row in db.execute("PRAGMA table_info(content_vectors)")}
    assert "chunk_date" in cols
    assert "hash" in cols
    assert "seq" in cols
    assert "model" in cols


@pytest.mark.unit
def test_ensure_vec_table_with_real_vec() -> None:
    """_ensure_vec_table creates the vec table when sqlite-vec is available."""
    try:
        import sqlite_vec
    except (ImportError, AttributeError):
        pytest.skip("sqlite-vec not available")

    db = sqlite3.connect(":memory:")
    db.enable_load_extension(True)
    db.load_extension(sqlite_vec.loadable_path())
    db.enable_load_extension(False)

    _ensure_vec_table(db, dims=128)
    row = db.execute("SELECT sql FROM sqlite_master WHERE name='vectors_vec'").fetchone()
    assert row is not None
    assert "float[128]" in row[0]


@pytest.mark.unit
def test_ensure_vec_table_recreates_on_dim_mismatch() -> None:
    """_ensure_vec_table drops and recreates if dimensions differ."""
    try:
        import sqlite_vec
    except (ImportError, AttributeError):
        pytest.skip("sqlite-vec not available")

    db = sqlite3.connect(":memory:")
    db.enable_load_extension(True)
    db.load_extension(sqlite_vec.loadable_path())
    db.enable_load_extension(False)

    _ensure_vec_table(db, dims=128)
    _ensure_vec_table(db, dims=256)  # should recreate
    row = db.execute("SELECT sql FROM sqlite_master WHERE name='vectors_vec'").fetchone()
    assert "float[256]" in row[0]


@pytest.mark.unit
def test_ensure_vec_table_creates_when_not_exists_mocked() -> None:
    """_ensure_vec_table creates vec table (mocked DB for no-sqlite-vec environments)."""
    from unittest.mock import MagicMock

    mock_db = MagicMock()
    # No existing table
    mock_db.execute.return_value.fetchone.return_value = None

    _ensure_vec_table(mock_db, dims=256)

    # Should have queried for existing table, then created
    calls = mock_db.execute.call_args_list
    assert any("sqlite_master" in str(c) for c in calls)
    assert any("CREATE VIRTUAL TABLE" in str(c) and "float[256]" in str(c) for c in calls)


@pytest.mark.unit
def test_ensure_vec_table_dimension_mismatch_mocked() -> None:
    """_ensure_vec_table drops and recreates on dimension mismatch (mocked)."""
    from unittest.mock import MagicMock

    mock_db = MagicMock()
    # Existing table with wrong dimensions
    mock_db.execute.return_value.fetchone.return_value = ("CREATE VIRTUAL TABLE vectors_vec USING vec0(float[128])",)

    _ensure_vec_table(mock_db, dims=256)

    calls_str = str(mock_db.execute.call_args_list)
    assert "DROP TABLE IF EXISTS vectors_vec" in calls_str
    assert "float[256]" in calls_str


@pytest.mark.unit
def test_ensure_vec_table_correct_dims_is_noop_mocked() -> None:
    """_ensure_vec_table does nothing when dimensions match (mocked)."""
    from unittest.mock import MagicMock

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = (
        "CREATE VIRTUAL TABLE vectors_vec USING vec0(hash_seq TEXT PRIMARY KEY, embedding float[256])",
    )

    _ensure_vec_table(mock_db, dims=256)

    calls_str = str(mock_db.execute.call_args_list)
    assert "DROP" not in calls_str
    assert "CREATE VIRTUAL TABLE" not in calls_str or "sqlite_master" in calls_str


@pytest.mark.unit
def test_ensure_vec_table_skips_if_correct_dims() -> None:
    """_ensure_vec_table is a no-op when table exists with correct dimensions."""
    try:
        import sqlite_vec
    except (ImportError, AttributeError):
        pytest.skip("sqlite-vec not available")

    db = sqlite3.connect(":memory:")
    db.enable_load_extension(True)
    db.load_extension(sqlite_vec.loadable_path())
    db.enable_load_extension(False)

    _ensure_vec_table(db, dims=128)
    sql_before = db.execute("SELECT sql FROM sqlite_master WHERE name='vectors_vec'").fetchone()[0]
    _ensure_vec_table(db, dims=128)  # should be a no-op
    sql_after = db.execute("SELECT sql FROM sqlite_master WHERE name='vectors_vec'").fetchone()[0]
    assert sql_before == sql_after


@pytest.mark.unit
def test_ensure_vec_table_no_existing_table() -> None:
    """_ensure_vec_table creates table when none exists."""
    try:
        import sqlite_vec
    except (ImportError, AttributeError):
        pytest.skip("sqlite-vec not available")

    db = sqlite3.connect(":memory:")
    db.enable_load_extension(True)
    db.load_extension(sqlite_vec.loadable_path())
    db.enable_load_extension(False)

    # Verify no table exists
    row = db.execute("SELECT sql FROM sqlite_master WHERE name='vectors_vec'").fetchone()
    assert row is None

    _ensure_vec_table(db, dims=64)
    row = db.execute("SELECT sql FROM sqlite_master WHERE name='vectors_vec'").fetchone()
    assert row is not None
    assert "float[64]" in row[0]
