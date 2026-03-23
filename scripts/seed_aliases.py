"""
Seed alias groups into /data/mnemosyne/entities.db.

Inserts canonical entities (if missing) then alias rows for 7 known alias groups.
Safe to re-run: uses INSERT OR IGNORE for canonicals, and upserts for aliases.

Note on schema constraints:
  - status CHECK (status IN ('active','archived')) — we use 'active' for alias rows
    and rely on canonical_id IS NOT NULL to indicate alias status.
  - type CHECK (type IN ('person','organisation','decision','concept','project'))
    — 'product' is not a valid type; we use 'concept' for product-type entities.
  - We expose a computed status='alias' in the seed output for clarity, but the
    DB stores 'active' with canonical_id set (the alias is discoverable via canonical_id).

Usage:
    python scripts/seed_aliases.py [--db /path/to/entities.db] [--dry-run]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB_PATH = "/data/mnemosyne/entities.db"

# DB-valid status value for alias rows.
# The alias relationship is expressed via canonical_id IS NOT NULL.
_ALIAS_STATUS = "active"

# Map 'product' → 'concept' etc. to satisfy schema CHECK constraint
_TYPE_MAP = {
    "product": "concept",
    "framework": "concept",
    "place": "concept",
    "person": "person",
    "organisation": "organisation",
    "decision": "decision",
    "concept": "concept",
    "project": "project",
}


def _db_type(t: str) -> str:
    return _TYPE_MAP.get(t, "concept")


# ---------------------------------------------------------------------------
# Canonical entities that must exist before aliases can reference them.
# (id, name, type)  — type uses raw value; _db_type() maps to schema-valid type
# ---------------------------------------------------------------------------
CANONICAL_ENTITIES = [
    ("bridgewater-engineering",  "BridgewaterEngineering", "concept"),
    ("microsoft",                "Microsoft",              "organisation"),
    ("dynamics-365",             "Dynamics 365",           "concept"),       # product → concept
    ("burger-palace-canonical",  "Burger Palace",          "organisation"),
    ("genesys-cloud",            "Genesys Cloud",          "concept"),        # product → concept
    ("copilot-studio",           "Copilot Studio",         "concept"),        # product → concept
    ("triad-consulting",         "Triad Consulting",       "organisation"),
]

# ---------------------------------------------------------------------------
# Alias groups:
# (alias_id, alias_name, canonical_id)
# ---------------------------------------------------------------------------
ALIAS_GROUPS = [
    ("bwe-c",                "BWE-C",                "bridgewater-engineering"),
    ("bwe-amp-c",            "BWE&C",                "bridgewater-engineering"),
    ("msft",                 "MSFT",                 "microsoft"),
    ("microsoft-corp",       "Microsoft Corporation","microsoft"),
    ("d365",                 "D365",                 "dynamics-365"),
    ("dynamics365",          "Dynamics365",          "dynamics-365"),
    ("microsoft-dynamics",   "Microsoft Dynamics",   "dynamics-365"),
    ("burger-palace",        "Burger Palace",        "burger-palace-canonical"),
    ("burger-palace-no-space","Burger Palace",       "burger-palace"),
    ("burgerpalace",         "BurgerPalace",         "burger-palace-canonical"),
    ("genesys",              "Genesys",              "genesys-cloud"),
    ("pva",                  "Power Virtual Agents", "copilot-studio"),
    ("power-virtual-agents", "Power Virtual Agents", "copilot-studio"),
    ("3-consulting",         "3 Consulting",         "triad-consulting"),
    ("triad-consulting-numeral", "3 Consulting",     "triad-consulting"),
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def seed(db_path: str, dry_run: bool = False) -> None:
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")

    now = _now_iso()

    # -----------------------------------------------------------------
    # 1. Ensure canonical entities exist
    # -----------------------------------------------------------------
    print("=== Ensuring canonical entities ===")
    for canon_id, canon_name, canon_type in CANONICAL_ENTITIES:
        existing = db.execute(
            "SELECT id, name, status FROM entities WHERE id = ?", (canon_id,)
        ).fetchone()
        if existing is not None:
            print(f"  OK (exists): {canon_id} — {canon_name}")
            continue

        db_t = _db_type(canon_type)
        markdown_path = f"06-Entities/{db_t}/{canon_id}.md"

        print(f"  INSERT canonical: {canon_id} — {canon_name} (type={db_t})")
        if not dry_run:
            db.execute(
                """
                INSERT OR IGNORE INTO entities
                    (id, type, name, status, markdown_path, summary, agent_scope,
                     created_at, updated_at, frequency, canonical_id)
                VALUES (?, ?, ?, 'active', ?, NULL, 'shared', ?, ?, 0, NULL)
                """,
                (canon_id, db_t, canon_name, markdown_path, now, now),
            )

    if not dry_run:
        db.commit()

    # -----------------------------------------------------------------
    # 2. Seed alias rows
    # -----------------------------------------------------------------
    print("\n=== Seeding alias rows ===")
    for alias_id, alias_name, canonical_id in ALIAS_GROUPS:
        # Get canonical entity's markdown_path and vault_path
        canon_row = db.execute(
            "SELECT type, markdown_path, vault_path FROM entities WHERE id = ?",
            (canonical_id,),
        ).fetchone()
        if canon_row is None:
            print(f"  WARN: canonical '{canonical_id}' not found — skipping alias '{alias_id}'")
            continue

        markdown_path = canon_row["markdown_path"]
        vault_path = canon_row["vault_path"]
        alias_type = canon_row["type"]  # same type as canonical

        # Check if alias already exists
        existing = db.execute(
            "SELECT id, status, canonical_id FROM entities WHERE id = ?", (alias_id,)
        ).fetchone()
        if existing is not None:
            existing_cid = existing["canonical_id"]
            if str(existing_cid) == canonical_id:
                print(f"  OK (exists): {alias_id} → {canonical_id}")
                continue
            # Update existing row to correct canonical_id
            print(f"  UPDATE: {alias_id} → {canonical_id}")
            if not dry_run:
                db.execute(
                    """
                    UPDATE entities
                    SET canonical_id = ?, name = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (canonical_id, alias_name, now, alias_id),
                )
            continue

        print(f"  INSERT alias: {alias_id} ({alias_name}) → {canonical_id}")
        if not dry_run:
            db.execute(
                """
                INSERT INTO entities
                    (id, type, name, status, markdown_path, vault_path, summary,
                     agent_scope, created_at, updated_at, frequency, canonical_id)
                VALUES (?, ?, ?, 'active', ?, ?, NULL, 'shared', ?, ?, 0, ?)
                """,
                (
                    alias_id, alias_type, alias_name,
                    markdown_path, vault_path,
                    now, now,
                    canonical_id,
                ),
            )

    if not dry_run:
        db.commit()
        print("\n✓ Seed complete.")
    else:
        print("\n(dry-run: no writes committed)")

    # -----------------------------------------------------------------
    # 3. Report
    # -----------------------------------------------------------------
    total = db.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    aliases_with_cid = db.execute(
        "SELECT COUNT(*) FROM entities WHERE canonical_id IS NOT NULL"
    ).fetchone()[0]
    print(f"\nDB totals: {total} entities, {aliases_with_cid} alias rows (canonical_id IS NOT NULL)")
    db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed alias groups into entities.db")
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="Path to entities.db")
    parser.add_argument("--dry-run", action="store_true", help="Print actions without writing")
    args = parser.parse_args()

    db_path = args.db
    if not Path(db_path).exists():
        print(f"ERROR: DB not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    seed(db_path, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
