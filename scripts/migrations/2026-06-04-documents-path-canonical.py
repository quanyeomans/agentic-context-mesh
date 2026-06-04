#!/usr/bin/env python3
"""GH #409 — add ``documents.path_canonical`` virtual generated column + index.

The search ``enrich`` phase calls ``SQLiteDocumentRepository.get_chunk_dates``
once per fused result batch. Pre-fix, that helper ran

    SELECT d.path, cv.chunk_date
    FROM content_vectors cv JOIN documents d ON d.hash = cv.hash
    WHERE cv.chunk_date IS NOT NULL AND (
        d.path LIKE '%path1' OR d.path LIKE '%path2' OR ...
    )

The ``LIKE '%suffix'`` pattern starts with a wildcard, so SQLite cannot
use any index on ``documents.path`` — every call full-scans 1.1M rows.
Production p50 enrich on the alpha9 VM was **14.1 seconds**, 81% of
search latency.

This migration replaces the LIKE scan with an indexed exact-match by
adding ``documents.path_canonical`` (a VIRTUAL generated column derived
from ``path``) and ``idx_documents_path_canonical``. The repository
query becomes ``WHERE d.path_canonical IN (?, ?, ...)`` — O(log N)
per probe, naturally bounded by the IN-list cardinality.

VIRTUAL was chosen over STORED because SQLite forbids STORED generated
columns in ``ALTER TABLE``, and the legacy 1.1M-row VM cannot afford a
table-copy. VIRTUAL columns are still indexable; the CREATE INDEX
materialises one entry per row at index-build time, which is what
gives the planner an O(log N) probe path.

Usage:

  # Preview the migration (no mutation, no commit).
  python3 scripts/migrations/2026-06-04-documents-path-canonical.py --dry-run

  # Apply the migration.
  python3 scripts/migrations/2026-06-04-documents-path-canonical.py

  # Apply against a non-default DB path.
  python3 scripts/migrations/2026-06-04-documents-path-canonical.py --db /path/to/index.sqlite

Idempotent — uses ``PRAGMA table_xinfo`` (which lists generated columns)
to detect a prior run, and ``CREATE INDEX IF NOT EXISTS`` for the index.
A re-run reports the existing state and exits 0.

Production VM runtime estimate (alpha9, 1.1M documents):
  - ``ALTER TABLE`` is metadata-only — milliseconds.
  - ``CREATE INDEX`` materialises one entry per row — single-digit
    minutes on the target storage class. The operation is a single
    write transaction; if the process is killed mid-build SQLite
    rolls back cleanly and the next invocation retries from scratch.

The canonical schema in ``kairix/core/db/schema.py`` carries the same
column + index DDL so a fresh deployment receives them without running
this migration; this script is the in-place upgrade path for production
DBs that pre-date the column.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

_COLUMN = "path_canonical"
_INDEX = "idx_documents_path_canonical"

_ALTER_TABLE_SQL = "ALTER TABLE documents ADD COLUMN path_canonical TEXT GENERATED ALWAYS AS (path) VIRTUAL"
_CREATE_INDEX_SQL = "CREATE INDEX IF NOT EXISTS idx_documents_path_canonical ON documents(path_canonical)"


def _column_exists(db: sqlite3.Connection, table: str, column: str) -> bool:
    """Return True iff ``column`` is registered on ``table``.

    Uses ``PRAGMA table_xinfo`` so generated columns (which
    ``PRAGMA table_info`` omits) are detected — the migration would
    otherwise crash with ``duplicate column name`` on a re-run.

    ``table`` is hardcoded by the caller (not user input) so the
    f-string interpolation is safe.
    """
    # safe: hardcoded table name from this migration script.
    return any(row[1] == column for row in db.execute(f"PRAGMA table_xinfo({table})"))


def _index_exists(db: sqlite3.Connection, name: str) -> bool:
    """Return True iff an index named ``name`` is registered in sqlite_master."""
    row = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _print_status(label: str, column_present: bool, index_present: bool) -> None:
    """Operator-facing status report — one line per object, easy to grep."""
    sys.stdout.write(f"[{label}] column documents.path_canonical:  {'present' if column_present else 'missing'}\n")
    sys.stdout.write(f"[{label}] index idx_documents_path_canonical: {'present' if index_present else 'missing'}\n")
    sys.stdout.flush()


def _apply(db: sqlite3.Connection) -> None:
    """Execute the ALTER TABLE + CREATE INDEX statements idempotently.

    The ALTER TABLE runs only when the column is missing; the CREATE
    INDEX is ``IF NOT EXISTS`` so it is safe to re-run unconditionally
    after the column exists.
    """
    if not _column_exists(db, "documents", _COLUMN):
        db.execute(_ALTER_TABLE_SQL)
    db.execute(_CREATE_INDEX_SQL)


def run_migration(*, db_path: Path, dry_run: bool) -> int:
    """Run the migration end-to-end against ``db_path``.

    Returns 0 on success (or when no work is needed), -1 when the DB
    path doesn't exist. Idempotent — a re-run on an already-migrated
    DB reports the existing state and exits 0.
    """
    if not db_path.exists():
        sys.stderr.write(
            f"error: db_path {db_path} does not exist. "
            f"fix: pass --db <path> pointing at the kairix sqlite index. "
            f"next: locate the file via "
            f"`python3 -c 'from kairix.paths import db_path; print(db_path())'`\n"
        )
        return -1

    db = sqlite3.connect(str(db_path))
    try:
        column_before = _column_exists(db, "documents", _COLUMN)
        index_before = _index_exists(db, _INDEX)
        _print_status("before", column_before, index_before)

        already_present = column_before and index_before
        if already_present:
            sys.stdout.write("[path-canonical] column + index already present — migration is a no-op.\n")
            return 0

        if dry_run:
            sys.stdout.write(
                "[path-canonical] DRY RUN — would add missing column and/or index. Re-run without --dry-run to apply.\n"
            )
            return 0

        _apply(db)
        db.commit()

        column_after = _column_exists(db, "documents", _COLUMN)
        index_after = _index_exists(db, _INDEX)
        _print_status("after", column_after, index_after)

        sys.stdout.write("[path-canonical] migration applied.\n")
        return 0
    finally:
        db.close()


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point — parses --dry-run + --db, dispatches the migration."""
    parser = argparse.ArgumentParser(
        description=(
            "GH #409 — add documents.path_canonical + idx_documents_path_canonical for indexed enrich-phase lookup."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report current state without mutating the database.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Path to the kairix sqlite index. Defaults to kairix.paths.db_path().",
    )
    args = parser.parse_args(argv)

    resolved_db: Path
    if args.db is not None:
        resolved_db = args.db
    else:
        from kairix.paths import db_path

        resolved_db = db_path()

    result = run_migration(db_path=resolved_db, dry_run=bool(args.dry_run))
    return 0 if result >= 0 else 1


if __name__ == "__main__":
    sys.exit(main())
