#!/usr/bin/env python3
"""GH #371 backfill — retag SharePoint docs leaked into the 'default' collection.

Production audit on 2026-06-01 found 1,032,859 documents in the
``default`` collection where the ``source_uri`` is a SharePoint URI
(``sharepoint://...`` or ``https://<tenant>.sharepoint.com/...``). The
leak was in ``kairix.worker._build_reextract_components`` which fell
back to ``entry.get("collection", "default")`` for the re-extract path
while the live-sync path correctly used the connector name. Every
re-extracted SharePoint document landed in the wrong collection.

The production fix (``resolve_collection_for_entry``) prevents new
leaks; this migration repairs the existing rows by updating their
``collection`` column from ``default`` to ``sharepoint``.

Usage:

  # Preview the change (no mutation, no commit).
  python3 scripts/migrations/2026-06-01-sharepoint-collection-backfill.py --dry-run

  # Apply the migration.
  python3 scripts/migrations/2026-06-01-sharepoint-collection-backfill.py

  # Apply against a non-default DB path.
  python3 scripts/migrations/2026-06-01-sharepoint-collection-backfill.py --db /path/to/index.sqlite

Idempotent: detects the migration's effect by counting rows that still
match the leak shape (``collection='default'`` with a sharepoint
source_uri). A re-run on already-migrated data reports zero rows to
update and exits cleanly.

DO NOT run this script as part of normal commits — the orchestrator
applies it once per production environment after verifying the
pre-flight counts on the live DB.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

# Match every documents row whose source_uri looks like SharePoint.
# Two shapes seen in the audit:
#   1. The connector's fallback source_link shape: ``sharepoint://items/<id>``
#      OR the cached envelope shape: ``sharepoint://<drive>/items/<id>``.
#   2. The Microsoft Graph web URL shape:
#      ``https://<tenant>.sharepoint.com/...``.
# A row is in scope iff EITHER pattern matches.
_SHAREPOINT_URI_PATTERNS: tuple[str, ...] = (
    "sharepoint://%",
    "https://%.sharepoint.com/%",
)

_LEGACY_COLLECTION = "default"
_TARGET_COLLECTION = "sharepoint"


def _count_leaked_rows(db: sqlite3.Connection) -> int:
    """Return the count of documents still matching the leak shape.

    Bounded by the documents.collection index (PK + UNIQUE(collection,
    path)) so the COUNT is fast even on multi-million-row DBs.
    """
    row = db.execute(
        """
        SELECT COUNT(*) FROM documents
        WHERE collection = ?
          AND (source_uri LIKE ? OR source_uri LIKE ?)
        """,
        (_LEGACY_COLLECTION, *_SHAREPOINT_URI_PATTERNS),
    ).fetchone()
    return int(row[0])


def _count_already_migrated(db: sqlite3.Connection) -> int:
    """Return the count of rows already in the target collection.

    Helps the operator sanity-check the before/after numbers — the
    target-collection count should grow by exactly ``_count_leaked_rows``
    after the UPDATE.
    """
    row = db.execute(
        "SELECT COUNT(*) FROM documents WHERE collection = ?",
        (_TARGET_COLLECTION,),
    ).fetchone()
    return int(row[0])


def _apply_backfill(db: sqlite3.Connection) -> int:
    """Update leaked rows to the target collection. Returns rows updated.

    UPDATE is wrapped in a single transaction so a mid-migration failure
    leaves the documents table in a known-good state — either every
    leaked row is retagged or none are.

    The UNIQUE(collection, path) constraint on documents could collide
    if a sharepoint-tagged sibling already exists at the same path; the
    query uses ``WHERE NOT EXISTS`` to skip those rows (which would be
    duplicate ingests the re-extract path created — the operator
    triages them separately).
    """
    cursor = db.execute(
        """
        UPDATE documents
        SET collection = ?
        WHERE collection = ?
          AND (source_uri LIKE ? OR source_uri LIKE ?)
          AND NOT EXISTS (
              SELECT 1 FROM documents d2
              WHERE d2.collection = ?
                AND d2.path = documents.path
          )
        """,
        (
            _TARGET_COLLECTION,
            _LEGACY_COLLECTION,
            *_SHAREPOINT_URI_PATTERNS,
            _TARGET_COLLECTION,
        ),
    )
    return cursor.rowcount or 0


def _count_collisions(db: sqlite3.Connection) -> int:
    """Return rows that cannot migrate due to a UNIQUE(collection, path) collision.

    Surfaces operator-facing follow-up: any row left after the UPDATE
    has a sharepoint-tagged sibling at the same path — the operator
    decides whether to keep the legacy row, delete it, or merge.
    """
    row = db.execute(
        """
        SELECT COUNT(*) FROM documents AS legacy
        WHERE legacy.collection = ?
          AND (legacy.source_uri LIKE ? OR legacy.source_uri LIKE ?)
          AND EXISTS (
              SELECT 1 FROM documents AS sibling
              WHERE sibling.collection = ?
                AND sibling.path = legacy.path
          )
        """,
        (
            _LEGACY_COLLECTION,
            *_SHAREPOINT_URI_PATTERNS,
            _TARGET_COLLECTION,
        ),
    ).fetchone()
    return int(row[0])


def _print_report(label: str, leaked: int, already: int, collisions: int) -> None:
    """Operator-facing report — one line per counter, easy to grep."""
    sys.stdout.write(f"[{label}] collection='default' with sharepoint source_uri:    {leaked:>10}\n")
    sys.stdout.write(f"[{label}] collection='sharepoint' (target, total):            {already:>10}\n")
    sys.stdout.write(f"[{label}] UNIQUE(collection, path) collisions (manual triage):{collisions:>10}\n")
    sys.stdout.flush()


def run_migration(*, db_path: Path, dry_run: bool) -> int:
    """Run the migration end-to-end against ``db_path``.

    Returns the count of rows updated (or, on ``--dry-run``, the count
    of rows that *would* be updated). Idempotent — a re-run on
    already-migrated data reports zero leaked rows and exits cleanly.
    """
    if not db_path.exists():
        sys.stderr.write(
            f"error: db_path {db_path} does not exist. "
            f"fix: pass --db <path> pointing at the kairix sqlite index. "
            f"next: locate the file via `python3 -c 'from kairix.paths import db_path; print(db_path())'`\n"
        )
        return -1

    db = sqlite3.connect(str(db_path))
    try:
        leaked_before = _count_leaked_rows(db)
        target_before = _count_already_migrated(db)
        collisions = _count_collisions(db)
        _print_report("before", leaked_before, target_before, collisions)

        if leaked_before == 0:
            sys.stdout.write(
                "[backfill] no leaked rows found — migration already applied or no SharePoint data present.\n"
            )
            return 0

        if dry_run:
            sys.stdout.write(
                f"[backfill] DRY RUN — would update {leaked_before - collisions} rows "
                f"(collisions: {collisions}). Re-run without --dry-run to apply.\n"
            )
            return leaked_before - collisions

        updated = _apply_backfill(db)
        db.commit()

        leaked_after = _count_leaked_rows(db)
        target_after = _count_already_migrated(db)
        _print_report("after", leaked_after, target_after, collisions)

        sys.stdout.write(f"[backfill] updated {updated} rows from 'default' to 'sharepoint'.\n")
        return updated
    finally:
        db.close()


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point — parses --dry-run + --db, dispatches the migration."""
    parser = argparse.ArgumentParser(
        description="GH #371 — retag SharePoint docs leaked into the 'default' collection.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report counts without mutating the database. Use to size the migration first.",
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
