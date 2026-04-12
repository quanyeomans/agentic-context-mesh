"""
Entity graph SQLite schema management for Mnemosyne.

Manages entities.db — a separate SQLite database from the QMD index,
storing entities, relationships, mentions, and facts.

Environment variable:
  KAIRIX_TEST_DB — override DB path for testing (avoids touching /data/kairix/)
"""

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 2
DEFAULT_DB_PATH = "/data/kairix/entities.db"

# Path to migrations directory, relative to this file
_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


class SchemaVersionError(Exception):
    """DB schema version is newer than this code supports — manual review required."""

    pass


def open_entities_db(path: str | None = None) -> sqlite3.Connection:
    """
    Open (or create) the entities SQLite database.

    If KAIRIX_TEST_DB env var is set, that path overrides both `path` and DEFAULT_DB_PATH.
    Configures WAL journal mode and calls ensure_schema() before returning.

    Args:
        path: Optional explicit path. Ignored if KAIRIX_TEST_DB is set.

    Returns:
        An open sqlite3.Connection with schema applied.
    """
    test_db = os.environ.get("KAIRIX_TEST_DB")
    if test_db:
        db_path = test_db
    elif path is not None:
        db_path = path
    else:
        db_path = DEFAULT_DB_PATH

    # Ensure parent directory exists
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row

    # Enable WAL mode for concurrent reads
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("PRAGMA busy_timeout=5000")

    ensure_schema(db)
    return db


def ensure_schema(db: sqlite3.Connection) -> None:
    """
    Ensure the entities DB schema is up to date.

    - If schema_version table is missing: applies migrations/001_initial.sql then migrates to latest
    - If version matches SCHEMA_VERSION: no-op (idempotent)
    - If version < SCHEMA_VERSION: runs incremental migrations
    - If version > SCHEMA_VERSION: raises SchemaVersionError

    Args:
        db: Open sqlite3.Connection to the entities database.

    Raises:
        SchemaVersionError: If the DB has a newer schema than this code supports.
    """
    # Check if schema_version table exists
    row = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'").fetchone()

    if row is None:
        # Fresh database — apply initial migration
        _apply_migration(db, "001_initial.sql")
        # version is now 1, fall through to incremental upgrades

    # Table exists — check version
    ver_row = db.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1").fetchone()
    if ver_row is None:
        # Table exists but empty — re-apply migration
        _apply_migration(db, "001_initial.sql")
        ver_row = db.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1").fetchone()

    current_version = ver_row[0]
    if current_version > SCHEMA_VERSION:
        raise SchemaVersionError(
            f"entities.db schema version {current_version} is newer than "
            f"supported version {SCHEMA_VERSION}. "
            "Update SCHEMA_VERSION in kairix/entities/schema.py after reviewing changes."
        )

    # Run incremental migrations
    if current_version < 2:
        _migrate_v1_to_v2(db)


def _apply_migration(db: sqlite3.Connection, filename: str) -> None:
    """Apply a SQL migration file to the database."""
    migration_path = _MIGRATIONS_DIR / filename
    if not migration_path.exists():
        raise FileNotFoundError(f"Migration file not found: {migration_path}")

    sql = migration_path.read_text()
    db.executescript(sql)


def _migrate_v1_to_v2(db: sqlite3.Connection) -> None:
    """Add vault_path, status (extended), frequency, last_seen, canonical_id to entities.
    Create entity_relationships table."""
    # SQLite supports ADD COLUMN but NOT adding columns with constraints that reference other tables
    # or NOT NULL without a default in one step. We add each column individually.

    # vault_path — nullable, no default
    _add_column_if_missing(db, "entities", "vault_path", "TEXT")

    # status already exists in v1 schema as TEXT NOT NULL DEFAULT 'active'
    # We do NOT re-add it; just extend allowed values via application logic.
    # (SQLite CHECK constraints can't be altered safely)

    # frequency
    _add_column_if_missing(db, "entities", "frequency", "INTEGER NOT NULL DEFAULT 0")

    # last_seen
    _add_column_if_missing(db, "entities", "last_seen", "TEXT")

    # canonical_id — self-referential FK (SQLite won't enforce FK on ADD COLUMN easily,
    # but we declare it for documentation; FK enforcement depends on PRAGMA foreign_keys)
    _add_column_if_missing(db, "entities", "canonical_id", "INTEGER REFERENCES entities(id)")

    # entity_relationships table
    db.executescript("""
        CREATE TABLE IF NOT EXISTS entity_relationships (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_a_id INTEGER NOT NULL REFERENCES entities(id),
            entity_b_id INTEGER NOT NULL REFERENCES entities(id),
            relationship_type TEXT NOT NULL,
            strength REAL NOT NULL DEFAULT 1.0,
            last_updated TEXT NOT NULL,
            UNIQUE(entity_a_id, entity_b_id, relationship_type)
        );
        CREATE INDEX IF NOT EXISTS idx_relationships_a ON entity_relationships(entity_a_id);
        CREATE INDEX IF NOT EXISTS idx_relationships_b ON entity_relationships(entity_b_id);
    """)

    # Bump schema version
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    db.execute("UPDATE schema_version SET version = 2, applied_at = ?", (now,))
    db.commit()


def _add_column_if_missing(db: sqlite3.Connection, table: str, column: str, col_def: str) -> None:
    """Add a column to a table only if it doesn't already exist (idempotent).

    INTERNAL migration helper — called exclusively from _migrate_v1_to_v2() with
    compile-time string literals. The dynamic SQL is intentional: PRAGMA table_info()
    and ALTER TABLE do not support parameter binding in SQLite. Do not call this
    function with user-supplied input.
    """
    existing = {row[1] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}  # nosec B608
    if column not in existing:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")  # nosec B608
        db.commit()
