"""
QMD SQLite schema validation and compatibility guard.

Pins to qmd@1.1.2. If QMD updates and the schema changes, this module
raises SchemaVersionError before touching any data.

Key QMD schema facts (verified against qmd@1.1.2):
  - content.doc   — document text (NOT 'body' — common assumption that's wrong)
  - content.hash  — SHA of document content, FK to documents.hash
  - documents.active — 1 = indexed, 0 = removed
  - vectors_vec   — sqlite-vec virtual table; embedding stored as packed float32 binary,
                    NOT JSON. Use struct.pack('<Nf', *vec).
  - hash_seq PK   — "{hash}_{seq}" e.g. "abc123_0", "abc123_1"

See QMD_COMPAT.md for the full schema and upgrade procedure.
"""

import json
import logging
import os
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

# Known paths for the sqlite-vec extension (vec0.so)
# QMD ships it as an npm package — these are the expected install locations
_SQLITE_VEC_SEARCH_PATHS = [
    # QMD pnpm install (standard)
    "/data/workspace/.tools/qmd/node_modules/.pnpm/sqlite-vec-linux-x64@0.1.7-alpha.2/node_modules/sqlite-vec-linux-x64/vec0.so",
    # QMD npm install fallback
    "/data/workspace/.tools/qmd/node_modules/sqlite-vec-linux-x64/vec0.so",
    # System install
    "/usr/local/lib/vec0.so",
    "/usr/lib/sqlite3/vec0.so",
]

# Allow override via env var
_SQLITE_VEC_ENV = "SQLITE_VEC_PATH"

# Bump this when QMD changes the schema and we've validated compatibility.
QMD_TESTED_VERSION = "1.1.2"

# Expected columns in content_vectors (QMD-owned; chunk_date is our migration via migrate_content_vectors)
EXPECTED_CONTENT_VECTORS_COLS = {"hash", "seq", "pos", "model", "embedded_at"}

# Expected columns in content (source chunks — NB: text is in 'doc' column, NOT 'body')
EXPECTED_CONTENT_COLS = {"hash", "doc", "created_at"}

EMBED_VECTOR_DIMS = 1536


class SchemaVersionError(Exception):
    """QMD schema has changed — manual review required before proceeding."""

    pass


class DBLockedError(Exception):
    """QMD SQLite is locked by another writer."""

    pass


def find_sqlite_vec() -> str | None:
    """
    Locate the sqlite-vec extension (vec0.so).
    Checks SQLITE_VEC_PATH env var first, then known install locations.
    Returns the path as a string, or None if not found.
    """
    # Explicit override
    env_path = os.environ.get(_SQLITE_VEC_ENV)
    if env_path and Path(env_path).exists():
        return env_path

    for path in _SQLITE_VEC_SEARCH_PATHS:
        if Path(path).exists():
            return path

    # Glob fallback — scan QMD node_modules for any vec0.so
    qmd_root = Path("/data/workspace/.tools/qmd/node_modules")
    if qmd_root.exists():
        matches = sorted(qmd_root.glob("**/vec0.so"))
        # Prefer x64 builds
        for m in matches:
            if "x64" in str(m):
                return str(m)
        if matches:
            return str(matches[0])

    return None


def load_sqlite_vec(db: sqlite3.Connection) -> None:
    """
    Load the sqlite-vec extension into an open SQLite connection.
    Raises RuntimeError with a helpful message if the extension cannot be found.

    Must be called before any vec0 virtual table operations.
    """
    vec_path = find_sqlite_vec()
    if not vec_path:
        raise RuntimeError(
            "sqlite-vec extension (vec0.so) not found. "
            "It ships with QMD — check your QMD install at /data/workspace/.tools/qmd/. "
            f"Or set {_SQLITE_VEC_ENV}=/path/to/vec0.so to specify the location explicitly."
        )
    db.enable_load_extension(True)
    db.load_extension(vec_path.removesuffix(".so"))  # SQLite strips .so itself on Linux
    db.enable_load_extension(False)


def get_qmd_db_path() -> Path:
    """Return path to QMD's SQLite index."""
    cache_dir = os.environ.get("QMD_CACHE_DIR", Path.home() / ".cache" / "qmd")
    path = Path(cache_dir) / "index.sqlite"
    if not path.exists():
        raise FileNotFoundError(f"QMD index not found at {path}. Run 'qmd embed' at least once to initialise.")
    return path


def validate_schema(db: sqlite3.Connection) -> None:
    """
    Validate that QMD's schema matches what we expect.
    Raises SchemaVersionError with a clear diff if anything changed.
    """
    errors = []

    # Check content_vectors columns
    cv_cols = {row[1] for row in db.execute("PRAGMA table_info(content_vectors)")}
    missing = EXPECTED_CONTENT_VECTORS_COLS - cv_cols
    if missing:
        errors.append(f"content_vectors missing columns: {missing}")

    # Check content columns
    c_cols = {row[1] for row in db.execute("PRAGMA table_info(content)")}
    missing = EXPECTED_CONTENT_COLS - c_cols
    if missing:
        errors.append(f"content missing columns: {missing}")

    # Check vectors_vec table exists (sqlite-vec virtual table)
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' OR type='shadow'")}
    virtual = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND sql LIKE '%vec0%'")}
    if "vectors_vec" not in tables and "vectors_vec" not in virtual:
        # It's OK if it doesn't exist yet — we'll create it
        pass

    if errors:
        raise SchemaVersionError(
            f"QMD schema mismatch (tested against qmd@{QMD_TESTED_VERSION}):\n"
            + "\n".join(f"  - {e}" for e in errors)
            + "\n\nUpdate QMD_TESTED_VERSION in schema.py after verifying compatibility."
        )


def ensure_vec_table(db: sqlite3.Connection, dims: int = EMBED_VECTOR_DIMS) -> None:
    """
    Ensure vectors_vec virtual table exists with the correct dimensions.
    If it exists with different dimensions, drops and recreates it.
    """
    cur = db.execute("SELECT sql FROM sqlite_master WHERE name='vectors_vec'").fetchone()

    if cur:
        existing_sql = cur[0] or ""
        expected_fragment = f"float[{dims}]"
        if expected_fragment in existing_sql:
            return  # Already correct
        # Dimension mismatch — drop and recreate
        db.execute("DROP TABLE IF EXISTS vectors_vec")

    db.execute(
        f"CREATE VIRTUAL TABLE vectors_vec USING vec0("
        f"hash_seq TEXT PRIMARY KEY, "
        f"embedding float[{dims}] distance_metric=cosine)"
    )
    db.commit()


def get_pending_chunks(db: sqlite3.Connection) -> list[dict[str, Any]]:
    """
    Return chunks that need embedding.
    Mirrors QMD's getHashesNeedingEmbedding() logic.

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
    """Append run metadata to ~/.cache/qmd/azure-embed-runs.json."""
    log_path = Path.home() / ".cache" / "qmd" / "azure-embed-runs.json"
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
        db:    Open sqlite3.Connection (QMD index — no sqlite-vec extension needed).
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
