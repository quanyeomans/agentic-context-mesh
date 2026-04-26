"""
Kairix database schema creation, validation, and migration.

The schema mirrors the structure previously owned by QMD, with the addition of
a ``kairix_meta`` table for schema versioning. Column names and types are
identical to ensure all existing queries work without modification.

Tables:
  - documents       — document registry (path, collection, hash, active flag)
  - content         — document text keyed by content hash
  - content_vectors — chunk metadata (hash, seq, pos, model, embedded_at, chunk_date)
  - documents_fts   — FTS5 full-text search index
  - vectors_vec     — sqlite-vec virtual table for vector similarity search
  - kairix_meta     — schema version tracking
"""

import logging
import sqlite3

from . import EMBED_VECTOR_DIMS

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1"


def create_schema(db: sqlite3.Connection, *, dims: int = EMBED_VECTOR_DIMS) -> None:
    """
    Create all kairix tables if they do not exist.

    Idempotent — safe to call on every startup. Uses IF NOT EXISTS for all
    DDL statements.

    Args:
        db:   Open sqlite3.Connection (sqlite-vec must already be loaded
              if ``vectors_vec`` is to be created).
        dims: Vector embedding dimensions (default: 1536).
    """
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

        CREATE INDEX IF NOT EXISTS idx_documents_hash ON documents(hash);
        CREATE INDEX IF NOT EXISTS idx_documents_collection ON documents(collection);
        CREATE INDEX IF NOT EXISTS idx_documents_active ON documents(active);
        CREATE INDEX IF NOT EXISTS idx_content_vectors_chunk_date ON content_vectors(chunk_date);
    """)

    # FTS5 — external content mode is not needed; we populate directly.
    # Check if it already exists before creating (FTS5 doesn't support IF NOT EXISTS).
    fts_exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='documents_fts'").fetchone()
    if not fts_exists:
        db.execute("CREATE VIRTUAL TABLE documents_fts USING fts5(filepath, title, doc, tokenize='porter unicode61')")

    # sqlite-vec virtual table
    _ensure_vec_table(db, dims)

    # Schema version
    db.execute(
        "INSERT OR IGNORE INTO kairix_meta (key, value) VALUES ('schema_version', ?)",
        (SCHEMA_VERSION,),
    )
    db.execute(
        "INSERT OR IGNORE INTO kairix_meta (key, value) VALUES ('created_by', 'kairix')",
    )

    db.commit()
    logger.info("db.schema: kairix schema initialised (version=%s, dims=%d)", SCHEMA_VERSION, dims)


def _ensure_vec_table(db: sqlite3.Connection, dims: int = EMBED_VECTOR_DIMS) -> None:
    """
    Ensure ``vectors_vec`` virtual table exists with the correct dimensions.

    If it exists with different dimensions, drops and recreates it.
    """
    cur = db.execute("SELECT sql FROM sqlite_master WHERE name='vectors_vec'").fetchone()

    if cur:
        existing_sql = cur[0] or ""
        expected_fragment = f"float[{dims}]"
        if expected_fragment in existing_sql:
            return  # Already correct
        # Dimension mismatch — drop and recreate
        logger.warning("db.schema: vectors_vec dimension mismatch — recreating with dims=%d", dims)
        db.execute("DROP TABLE IF EXISTS vectors_vec")

    db.execute(
        f"CREATE VIRTUAL TABLE vectors_vec USING vec0("
        f"hash_seq TEXT PRIMARY KEY, "
        f"embedding float[{dims}] distance_metric=cosine)"
    )
    db.commit()


def validate_schema(db: sqlite3.Connection) -> list[str]:
    """
    Validate the database schema against expectations.

    Returns a list of error strings. Empty list means schema is valid.
    """
    errors: list[str] = []

    # Check required tables
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")}
    for required in ("documents", "content", "content_vectors"):
        if required not in tables:
            errors.append(f"missing table: {required}")

    if errors:
        return errors  # Can't check columns if tables are missing

    # Check critical columns
    expected_cols = {
        "documents": {"id", "collection", "path", "hash", "active"},
        "content": {"hash", "doc"},
        "content_vectors": {"hash", "seq", "pos"},
    }
    for table, expected in expected_cols.items():
        actual = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
        missing = expected - actual
        if missing:
            errors.append(f"{table} missing columns: {missing}")

    return errors


def migrate(db: sqlite3.Connection) -> None:
    """
    Run all pending migrations. Idempotent — safe to call on every startup.

    Currently handles:
      - Adding chunk_date column to content_vectors (if missing)
      - Creating kairix_meta table (if missing)
    """
    # Ensure kairix_meta exists
    db.execute("""
        CREATE TABLE IF NOT EXISTS kairix_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    # chunk_date migration (originally from embed/schema.py migrate_content_vectors)
    existing = {row[1] for row in db.execute("PRAGMA table_info(content_vectors)")}
    if "chunk_date" not in existing:
        db.execute("ALTER TABLE content_vectors ADD COLUMN chunk_date TEXT")
        db.commit()
        logger.info("db.schema: migration — added chunk_date column to content_vectors")

    # Ensure indexes exist (idempotent) — only if the tables exist
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "documents" in tables:
        db.executescript("""
            CREATE INDEX IF NOT EXISTS idx_documents_hash ON documents(hash);
            CREATE INDEX IF NOT EXISTS idx_documents_collection ON documents(collection);
            CREATE INDEX IF NOT EXISTS idx_documents_active ON documents(active);
        """)
    if "content_vectors" in tables:
        db.execute("CREATE INDEX IF NOT EXISTS idx_content_vectors_chunk_date ON content_vectors(chunk_date)")
