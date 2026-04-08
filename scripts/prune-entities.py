#!/usr/bin/env python3
"""
prune-entities.py — Remove stale/noise entities from entities.db.

An entity is stale if its vault stub file no longer exists on disk,
or if its frequency is 0 (never mentioned in the vault).

This script implements the stubs-as-source-of-truth pattern (ADR: entity pipeline
inversion): the entities DB should reflect exactly what is curated in vault stubs.
Entities that were auto-extracted or whose stub was deleted are candidates for removal.

Usage:
    python scripts/prune-entities.py [--vault-root PATH] [--db-path PATH] [--execute]

Defaults:
    --vault-root  $MNEMOSYNE_VAULT_ROOT or /path/to/vault
    --db-path     $MNEMOSYNE_ENTITIES_DB or /path/to/entities.db
    --execute     dry-run by default (omit flag to preview only)
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path
from typing import NamedTuple


class EntityRow(NamedTuple):
    id: int
    name: str
    type: str
    markdown_path: str | None
    frequency: int


class PruneReason:
    FILE_MISSING = "file_missing"
    ZERO_FREQUENCY = "zero_frequency"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prune stale/noise entities from entities.db.",
    )
    parser.add_argument(
        "--vault-root",
        default=os.environ.get("MNEMOSYNE_VAULT_ROOT", "/path/to/vault"),
        help="Root of the Obsidian vault (default: $MNEMOSYNE_VAULT_ROOT)",
    )
    parser.add_argument(
        "--db-path",
        default=os.environ.get("MNEMOSYNE_ENTITIES_DB", "/path/to/entities.db"),
        help="Path to entities.db (default: $MNEMOSYNE_ENTITIES_DB)",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        default=False,
        help="Apply deletions (default: dry-run, print report only)",
    )
    return parser.parse_args()


def load_active_entities(db: sqlite3.Connection) -> list[EntityRow]:
    """Load all active entities from the database."""
    cursor = db.execute(
        "SELECT id, name, type, markdown_path, frequency FROM entities WHERE status='active'"
    )
    rows = cursor.fetchall()
    return [EntityRow(id=r[0], name=r[1], type=r[2], markdown_path=r[3], frequency=r[4]) for r in rows]


def classify_entities(
    entities: list[EntityRow],
    vault_root: str,
) -> tuple[list[tuple[EntityRow, str]], list[EntityRow]]:
    """
    Classify entities into delete candidates and keepers.

    An entity is stale if:
    - Its markdown_path does not exist on disk (stub was deleted or never created)
    - Its frequency is 0 (never mentioned in the vault)

    Returns:
        (to_delete, to_keep)
    """
    to_delete: list[tuple[EntityRow, str]] = []
    to_keep: list[EntityRow] = []

    vault = Path(vault_root)

    for entity in entities:
        if entity.markdown_path:
            stub_path = vault / entity.markdown_path
            if not stub_path.exists():
                to_delete.append((entity, PruneReason.FILE_MISSING))
                continue

        if entity.frequency == 0:
            to_delete.append((entity, PruneReason.ZERO_FREQUENCY))
        else:
            to_keep.append(entity)

    return to_delete, to_keep


def print_dry_run_report(
    to_delete: list[tuple[EntityRow, str]],
    to_keep: list[EntityRow],
) -> None:
    """Print a dry-run report without making any changes."""
    total = len(to_delete) + len(to_keep)
    print(f"\nEntities scanned:  {total}")
    print(f"  To delete:       {len(to_delete)}")
    print(f"  To keep:         {len(to_keep)}")

    if to_delete:
        print("\n--- ENTITIES TO DELETE ---")
        col_name = max((len(e.name) for e, _ in to_delete), default=4)
        col_type = max((len(e.type or "") for e, _ in to_delete), default=4)
        header = f"  {'NAME':<{col_name}}  {'TYPE':<{col_type}}  REASON"
        print(header)
        print("  " + "-" * (col_name + col_type + 10))
        for entity, reason in sorted(to_delete, key=lambda x: (x[1], x[0].type or "", x[0].name)):
            print(f"  {entity.name:<{col_name}}  {entity.type or '':<{col_type}}  {reason}")

    if to_keep:
        print("\n--- ENTITIES TO KEEP ---")
        col_name = max((len(e.name) for e in to_keep), default=4)
        col_type = max((len(e.type or "") for e in to_keep), default=4)
        header = f"  {'NAME':<{col_name}}  {'TYPE':<{col_type}}  FREQUENCY"
        print(header)
        print("  " + "-" * (col_name + col_type + 12))
        for entity in sorted(to_keep, key=lambda e: (-e.frequency, e.type or "", e.name)):
            print(f"  {entity.name:<{col_name}}  {entity.type or '':<{col_type}}  {entity.frequency}")

    print("\nRun with --execute to apply deletions.")


def cascade_delete_entity(db: sqlite3.Connection, entity_id: int) -> None:
    """Delete an entity and all related rows in cascade order."""
    db.execute("DELETE FROM entity_mentions WHERE entity_id = ?", (entity_id,))
    db.execute(
        "DELETE FROM entity_relationships WHERE entity_a_id = ? OR entity_b_id = ?",
        (entity_id, entity_id),
    )
    db.execute("DELETE FROM entity_facts WHERE entity_id = ?", (entity_id,))
    db.execute("DELETE FROM entities WHERE id = ?", (entity_id,))


def execute_pruning(
    db: sqlite3.Connection,
    to_delete: list[tuple[EntityRow, str]],
    total_before: int,
) -> None:
    """Apply cascade deletions and print a summary."""
    print(f"\nDeleting {len(to_delete)} entities...")

    for entity, reason in to_delete:
        print(f"  Deleting: {entity.name!r} ({entity.type}) — {reason}")
        cascade_delete_entity(db, entity.id)

    db.commit()

    cursor = db.execute("SELECT COUNT(*) FROM entities WHERE status='active'")
    count_after = cursor.fetchone()[0]

    print(f"\nDone. Entities: {total_before} → {count_after}.")
    print("Run `mnemosyne entity extract --from-stubs` to re-seed.")


def main() -> None:
    args = parse_args()

    try:
        db_path = args.db_path
        vault_root = args.vault_root

        if not Path(db_path).exists():
            print(f"ERROR: Database not found: {db_path}", file=sys.stderr)
            sys.exit(1)

        db = sqlite3.connect(db_path)
        db.execute("PRAGMA foreign_keys = ON")
        db.row_factory = sqlite3.Row

        print(f"Database:   {db_path}")
        print(f"Vault root: {vault_root}")
        print(f"Mode:       {'EXECUTE' if args.execute else 'DRY-RUN'}")

        entities = load_active_entities(db)
        total_before = len(entities)

        if total_before == 0:
            print("\nNo active entities found. Nothing to do.")
            db.close()
            return

        to_delete, to_keep = classify_entities(entities, vault_root)

        if args.execute:
            if not to_delete:
                print(f"\nNo stale entities found. All {total_before} entities are healthy.")
            else:
                execute_pruning(db, to_delete, total_before)
        else:
            print_dry_run_report(to_delete, to_keep)

        db.close()

    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
