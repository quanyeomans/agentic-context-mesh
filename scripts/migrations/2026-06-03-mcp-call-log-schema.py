#!/usr/bin/env python3
"""Issue #398 (Workstream D) — create the ``mcp_call_log`` table.

The FastMCP server today emits ``Processing request of type ...`` log
lines that do not name the tool, and brief failures show up only as
``WARNING run_brief failed: ...`` in container logs. There is no
queryable per-call surface for operators investigating MCP latency or
error rates.

This migration creates ``mcp_call_log`` — one row per MCP tool call,
written by ``kairix.agents.mcp.errors.async_tool_handler`` — plus
two indexes that support the canonical operator queries
(``WHERE tool = ?`` + recent-window scans).

Usage:

  # Preview the migration (no mutation, no commit).
  python3 scripts/migrations/2026-06-03-mcp-call-log-schema.py --dry-run

  # Apply the migration.
  python3 scripts/migrations/2026-06-03-mcp-call-log-schema.py

  # Apply against a non-default DB path.
  python3 scripts/migrations/2026-06-03-mcp-call-log-schema.py --db /path/to/index.sqlite

Idempotent: every DDL statement uses ``IF NOT EXISTS``. A re-run on
an already-migrated DB reports ``mcp_call_log already present`` and
exits cleanly.

The canonical schema in ``kairix/core/db/schema.py`` carries the same
``CREATE TABLE`` so a fresh deployment receives the table without
running this migration; this script is the in-place upgrade path
for production DBs that pre-date the table.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

_TABLE = "mcp_call_log"

# DDL kept verbatim alongside the canonical copy in
# kairix/core/db/schema.py. Any change here must land in both sites.
_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS mcp_call_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL,
    tool            TEXT NOT NULL,
    agent           TEXT,
    latency_ms      INTEGER NOT NULL,
    success         INTEGER NOT NULL,
    error_class     TEXT,
    payload_hash    TEXT
);
"""

_CREATE_INDEX_TOOL_TIME_SQL = "CREATE INDEX IF NOT EXISTS idx_mcp_call_log_tool_time ON mcp_call_log(tool, timestamp);"
_CREATE_INDEX_TIME_SQL = "CREATE INDEX IF NOT EXISTS idx_mcp_call_log_time ON mcp_call_log(timestamp);"


def _table_exists(db: sqlite3.Connection, name: str) -> bool:
    """Return True iff a table named ``name`` is registered in sqlite_master."""
    row = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _index_exists(db: sqlite3.Connection, name: str) -> bool:
    """Return True iff an index named ``name`` is registered in sqlite_master."""
    row = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _print_status(label: str, table_present: bool, tool_idx: bool, time_idx: bool) -> None:
    """Operator-facing status report — one line per object, easy to grep."""
    sys.stdout.write(f"[{label}] table mcp_call_log:                 {'present' if table_present else 'missing'}\n")
    sys.stdout.write(f"[{label}] index idx_mcp_call_log_tool_time:   {'present' if tool_idx else 'missing'}\n")
    sys.stdout.write(f"[{label}] index idx_mcp_call_log_time:        {'present' if time_idx else 'missing'}\n")
    sys.stdout.flush()


def _apply(db: sqlite3.Connection) -> None:
    """Execute the CREATE TABLE + two CREATE INDEX statements idempotently."""
    db.execute(_CREATE_TABLE_SQL)
    db.execute(_CREATE_INDEX_TOOL_TIME_SQL)
    db.execute(_CREATE_INDEX_TIME_SQL)


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
        table_before = _table_exists(db, _TABLE)
        tool_idx_before = _index_exists(db, "idx_mcp_call_log_tool_time")
        time_idx_before = _index_exists(db, "idx_mcp_call_log_time")
        _print_status("before", table_before, tool_idx_before, time_idx_before)

        already_present = table_before and tool_idx_before and time_idx_before
        if already_present:
            sys.stdout.write("[mcp-call-log] mcp_call_log + both indexes already present — migration is a no-op.\n")
            return 0

        if dry_run:
            sys.stdout.write(
                "[mcp-call-log] DRY RUN — would create missing table/indexes. Re-run without --dry-run to apply.\n"
            )
            return 0

        _apply(db)
        db.commit()

        table_after = _table_exists(db, _TABLE)
        tool_idx_after = _index_exists(db, "idx_mcp_call_log_tool_time")
        time_idx_after = _index_exists(db, "idx_mcp_call_log_time")
        _print_status("after", table_after, tool_idx_after, time_idx_after)

        sys.stdout.write("[mcp-call-log] migration applied.\n")
        return 0
    finally:
        db.close()


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point — parses --dry-run + --db, dispatches the migration."""
    parser = argparse.ArgumentParser(
        description="Issue #398 — create the mcp_call_log table + indexes for MCP per-call observability.",
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
