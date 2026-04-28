"""
Kairix SQLite schema utilities — embedding, vector storage, and chunk metadata.

Delegates database path resolution and sqlite-vec extension loading to
``kairix.db``. This module retains embedding-specific schema functions
(ensure_vec_table, get_pending_chunks, etc.) and is the primary import
for the embed pipeline.

Key schema facts:
  - content.doc   — document text (NOT 'body')
  - content.hash  — SHA of document content, FK to documents.hash
  - documents.active — 1 = indexed, 0 = removed
  - vectors_vec   — sqlite-vec virtual table; embedding stored as packed float32 binary.
  - hash_seq PK   — "{hash}_{seq}" e.g. "abc123_0", "abc123_1"
"""

import json
import logging
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

from kairix.db import EMBED_VECTOR_DIMS, get_db_path, load_extensions

# Re-export EMBED_VECTOR_DIMS for backward compatibility
__all__ = [
    "EMBED_VECTOR_DIMS",
    "DBLockedError",
    "SchemaVersionError",
    "ensure_vec_table",
    "find_sqlite_vec",
    "get_all_chunks_needing_embedding",
    "get_date_filtered_paths",
    "get_db_path",
    "get_pending_chunks",
    "load_sqlite_vec",
    "migrate_content_vectors",
    "save_run_log",
    "validate_schema",
]


class SchemaVersionError(Exception):
    """Database schema is incompatible — manual review required."""

    pass


class DBLockedError(Exception):
    """SQLite is locked by another writer."""

    pass


def find_sqlite_vec() -> str | None:
    """Locate the sqlite-vec extension. Delegates to ``kairix.db._find_sqlite_vec()``."""
    from kairix.db import _find_sqlite_vec

    return _find_sqlite_vec()


def load_sqlite_vec(db: sqlite3.Connection) -> None:
    """Load sqlite-vec extension. Delegates to ``kairix.db.load_extensions()``."""
    load_extensions(db)


# get_db_path is re-exported from kairix.db for backwards compatibility.
# Callers should import from kairix.db directly.


def validate_schema(db: sqlite3.Connection) -> None:
    """
    Validate the database schema.

    Delegates to ``kairix.db.schema.validate_schema()`` and raises
    SchemaVersionError if any issues are found.
    """
    from kairix.db.schema import validate_schema as _validate

    errors = _validate(db)
    if errors:
        raise SchemaVersionError("Database schema validation failed:\n" + "\n".join(f"  - {e}" for e in errors))


def ensure_vec_table(db: sqlite3.Connection, dims: int = EMBED_VECTOR_DIMS) -> None:
    """Ensure vec0 virtual table exists. Delegates to kairix.db.schema."""
    from kairix.db.schema import _ensure_vec_table

    return _ensure_vec_table(db, dims)


def get_pending_chunks(db: sqlite3.Connection) -> list[dict[str, Any]]:
    """
    Return chunks that need embedding.
    Mirrors kairix's getHashesNeedingEmbedding() logic.

    Returns list of dicts: {hash, text, path}
    """
    rows = db.execute("""
        SELECT c.hash, c.doc, d.path
        FROM content c
        JOIN documents d ON c.hash = d.hash
        LEFT JOIN content_vectors v ON c.hash = v.hash AND v.seq = 0
        WHERE v.hash IS NULL
          AND d.active = 1
          AND c.doc IS NOT NULL
          AND length(c.doc) > 0
        GROUP BY c.hash
    """).fetchall()

    chunks = []
    for row in rows:
        content_hash, doc, path = row
        chunks.append(
            {
                "hash": content_hash,
                "body": doc,
                "path": path,
            }
        )
    return chunks


def get_all_chunks_needing_embedding(db: sqlite3.Connection) -> list[dict[str, Any]]:
    """
    Return all (hash, seq, pos, text) tuples from content_vectors
    that exist in content_vectors but have no entry in vectors_vec.
    Used for incremental catch-up after partial failures.
    """
    rows = db.execute("""
        SELECT cv.hash, cv.seq, cv.pos, c.doc
        FROM content_vectors cv
        JOIN content c ON c.hash = cv.hash
        LEFT JOIN vectors_vec vv ON vv.hash_seq = (cv.hash || '_' || cv.seq)
        WHERE vv.hash_seq IS NULL
          AND c.doc IS NOT NULL
    """).fetchall()

    return [{"hash": r[0], "seq": r[1], "pos": r[2], "body": r[3]} for r in rows]


def save_run_log(entry: dict[str, Any]) -> None:
    """Append run metadata to the kairix cache directory."""
    # Use kairix cache dir
    log_path = Path.home() / ".cache" / "kairix" / "embed-runs.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    runs = []
    if log_path.exists():
        try:
            runs = json.loads(log_path.read_text())
        except (json.JSONDecodeError, OSError):
            runs = []
    runs.append(entry)
    # Keep last 90 runs
    runs = runs[-90:]
    log_path.write_text(json.dumps(runs, indent=2))


_logger = logging.getLogger(__name__)


def migrate_content_vectors(db: sqlite3.Connection) -> None:
    """
    Idempotent migration: add chunk_date column to content_vectors if missing.

    This migration is additive-only (ALTER TABLE ADD COLUMN). The column is
    nullable with no default so existing rows are unaffected.

    Safe to call on every startup -- it is a no-op when the column already
    exists.
    """
    existing = {row[1] for row in db.execute("PRAGMA table_info(content_vectors)")}
    if "chunk_date" in existing:
        return

    db.execute("ALTER TABLE content_vectors ADD COLUMN chunk_date TEXT")
    db.commit()
    _logger.info("Migration: added chunk_date column to content_vectors")


def get_date_filtered_paths(
    db: sqlite3.Connection,
    start: date | None,
    end: date | None,
) -> frozenset[str]:
    """
    Return vault-relative paths whose chunk_date falls within [start, end].

    Used by TMP-2 to pre-filter hybrid search results for TEMPORAL queries.
    When both start and end are None, returns an empty frozenset immediately
    (caller treats empty as no-filter to avoid zero-result searches during
    the chunk_date backfill transition period).

    Falls back to empty frozenset on any DB error rather than raising.

    Args:
        db:    Open sqlite3.Connection (kairix index — no sqlite-vec extension needed).
        start: Lower bound (inclusive). None means no lower bound.
        end:   Upper bound (inclusive). None means no upper bound.

    Returns:
        frozenset of vault-relative document paths with chunk_date in [start, end].
        Empty frozenset means no dated chunks found; caller must not filter results.
    """
    if start is None and end is None:
        return frozenset()

    conditions = ["cv.chunk_date IS NOT NULL"]
    params: list[str] = []
    if start is not None:
        conditions.append("cv.chunk_date >= ?")
        params.append(start.isoformat())
    if end is not None:
        conditions.append("cv.chunk_date <= ?")
        params.append(end.isoformat())

    try:
        rows = db.execute(
            "SELECT DISTINCT d.path "
            "FROM content_vectors cv "
            "JOIN documents d ON d.hash = cv.hash "
            "WHERE " + " AND ".join(conditions),
            params,
        ).fetchall()
        return frozenset(r[0] for r in rows)
    except Exception as exc:
        _logger.warning("get_date_filtered_paths: query failed — %s", exc)
        return frozenset()
