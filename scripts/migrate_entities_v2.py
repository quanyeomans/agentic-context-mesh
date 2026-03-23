#!/usr/bin/env python3
"""
migrate_entities_v2.py — Migrate /data/mnemosyne/entities.db to schema v2.

Steps:
  1. Opens /data/mnemosyne/entities.db
  2. Runs the v1→v2 schema migration (idempotent)
  3. Sets vault_path for each known entity
  4. Reports: N entities migrated, N vault_paths set
"""

import sys
from pathlib import Path

# Allow running from repo root without installation
sys.path.insert(0, str(Path(__file__).parent.parent))

from mnemosyne.entities.schema import open_entities_db

DB_PATH = "/data/mnemosyne/entities.db"

VAULT_PATHS: dict[str, str] = {
    "Alex Jordan": "02-Areas/Career/",
    "Triad Consulting": "02-Areas/Triad-Consulting/",
    "Builder": "04-Agent-Knowledge/builder/",
    "Shape": "04-Agent-Knowledge/shape/",
    "Mnemosyne": "01-Projects/202603-Mnemosyne/",
    "tc-productivity": "02-Areas/Triad-Consulting/",
    "Bower Bird": "02-Areas/Triad-Consulting/",
    "QMD": "04-Agent-Knowledge/shared/",
    "OpenClaw": "04-Agent-Knowledge/shared/",
    "Azure": "04-Agent-Knowledge/shared/",
    "Leslie": "02-Areas/Career/Network/",
    "qmd-azure-embed": "01-Projects/202603-Mnemosyne/",
    "ADR-M01": "01-Projects/202603-Mnemosyne/DECISIONS.md",
    "ADR-M02": "01-Projects/202603-Mnemosyne/DECISIONS.md",
}


def main() -> None:
    print(f"Opening {DB_PATH} …")

    if not Path(DB_PATH).exists():
        print(f"ERROR: {DB_PATH} does not exist", file=sys.stderr)
        sys.exit(1)

    db = open_entities_db(path=DB_PATH)

    # Check current version after migration
    ver = db.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1").fetchone()[0]
    print(f"Schema version after migration: {ver}")

    # Count total entities
    total = db.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    print(f"Total entities in DB: {total}")

    # Set vault_path for known entities
    vault_paths_set = 0
    not_found: list[str] = []

    for name, vault_path in VAULT_PATHS.items():
        # Try exact match first, then prefix/LIKE match for partial names (e.g. "ADR-M01")
        row = db.execute("SELECT id FROM entities WHERE name = ?", (name,)).fetchone()
        if row is None:
            row = db.execute(
                "SELECT id FROM entities WHERE name LIKE ?", (f"{name}%",)
            ).fetchone()
        if row is None:
            not_found.append(name)
            continue
        entity_id = row[0]
        db.execute("UPDATE entities SET vault_path = ? WHERE id = ?", (vault_path, entity_id))
        vault_paths_set += 1

    db.commit()

    print(f"\n✅ Schema migration: v{ver}")
    print(f"✅ vault_paths set: {vault_paths_set}/{len(VAULT_PATHS)}")

    if not_found:
        print(f"⚠️  Entities not found in DB ({len(not_found)}): {', '.join(not_found)}")

    db.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
