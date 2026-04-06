#!/usr/bin/env python3
"""
seed-entity-relations.py -- Seed entity_relationships from vault entity stub wikilinks.

Scans all entity stubs in /data/obsidian-vault/04-Agent-Knowledge/entities/,
finds ## Relationships sections, resolves wikilinks via entity_lookup(),
and writes typed relations to the entity_relationships table in entities.db.

Usage:
    python seed-entity-relations.py [--dry-run]
"""
import argparse
import re
import sqlite3
import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, "/data/tools/qmd-azure-embed")

from mnemosyne.entities.graph import entity_lookup
from mnemosyne.entities.schema import open_entities_db

VAULT_ENTITIES_DIR = Path("/data/obsidian-vault/04-Agent-Knowledge/entities")

RELATIONSHIP_PATTERNS = [
    (re.compile(r"\b(founder|founded|co-founder)\b", re.I), "FOUNDED_BY"),
    (re.compile(r"\b(employer|employs|employed|works at|working at|employee)\b", re.I), "EMPLOYED_BY"),
    (re.compile(r"\b(client|customer)\b", re.I), "CLIENT_OF"),
    (re.compile(r"\b(owns|owner|ownership)\b", re.I), "OWNED_BY"),
    (re.compile(r"\b(leads|lead|director|manages|head of|chief)\b", re.I), "LEADS"),
    (re.compile(r"\b(partner|partnership)\b", re.I), "PARTNER_OF"),
    (re.compile(r"\b(investor|invests|invested|backed by|backer)\b", re.I), "INVESTED_BY"),
]


def classify_relationship(context_text: str) -> str:
    for pattern, rel_type in RELATIONSHIP_PATTERNS:
        if pattern.search(context_text):
            return rel_type
    return "RELATED_TO"


def extract_entity_name_from_path(stub_path: Path) -> str:
    return stub_path.stem.replace("-", " ").title()


def parse_relationships_section(text: str) -> list:
    results = []
    in_section = False
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"^## Relationships", stripped):
            in_section = True
            continue
        if in_section:
            if re.match(r"^## ", stripped):
                break
            wikilinks = re.findall(r"\[\[([^\]]+)\]\]", line)
            for wl in wikilinks:
                target = wl.split("/")[-1].strip()
                results.append((target, line))
    return results


def resolve_entity(name: str, db: sqlite3.Connection):
    for variant in [name, name.lower(), name.title(), name.upper()]:
        result = entity_lookup(variant, db)
        if result:
            return result.id
    # Try slug: "Tc Productivity" -> "tc-productivity"
    slug_variant = name.lower().replace(" ", "-")
    result = entity_lookup(slug_variant, db)
    if result:
        return result.id
    # Try raw stem if it looks like a slug
    if "-" in name:
        result = entity_lookup(name.replace("-", " ").title(), db)
        if result:
            return result.id
    return None


def seed_relations(dry_run: bool = False) -> None:
    db = open_entities_db()
    cur = db.cursor()

    cur.execute("SELECT COUNT(*) FROM entity_relationships")
    existing_count = cur.fetchone()[0]
    print(f"Existing relations in DB: {existing_count}")

    stubs = list(VAULT_ENTITIES_DIR.rglob("*.md"))
    print(f"Scanning {len(stubs)} entity stubs in {VAULT_ENTITIES_DIR}")

    inserted = 0
    skipped_no_rel_section = 0
    skipped_unresolved_source = 0
    skipped_unresolved_target = 0
    skipped_duplicate = 0
    rows = []

    for stub_path in sorted(stubs):
        text = stub_path.read_text(encoding="utf-8")

        if "## Relationships" not in text:
            skipped_no_rel_section += 1
            continue

        source_name = extract_entity_name_from_path(stub_path)
        source_id = resolve_entity(source_name, db)
        # Fallback: try raw stem (e.g. "tc-productivity")
        if source_id is None:
            source_id = resolve_entity(stub_path.stem, db)
        if source_id is None:
            print(f"  [WARN] Cannot resolve source: {source_name!r} / {stub_path.stem!r}")
            skipped_unresolved_source += 1
            continue

        rels = parse_relationships_section(text)
        if not rels:
            continue

        print(f"\n  {stub_path.name} -> id={source_id} ({source_name})")
        for target_name, context_line in rels:
            target_id = resolve_entity(target_name, db)
            if target_id is None:
                print(f"    [SKIP] Unresolved target: {target_name!r}")
                skipped_unresolved_target += 1
                continue

            rel_type = classify_relationship(context_line)
            print(f"    -> [{rel_type}] {target_name} (id={target_id}) | {context_line.strip()[:80]}")

            key = (source_id, target_id, rel_type)
            if key not in [(r[0], r[1], r[2]) for r in rows]:
                rows.append(key)

    print(f"\nRelations to insert: {len(rows)}")

    if not dry_run:
        now = datetime.now(timezone.utc).isoformat()
        for source_id, target_id, rel_type in rows:
            try:
                cur.execute(
                    "INSERT OR IGNORE INTO entity_relationships "
                    "(entity_a_id, entity_b_id, relationship_type, strength, last_updated) "
                    "VALUES (?, ?, ?, 1.0, ?)",
                    (source_id, target_id, rel_type, now),
                )
                if cur.rowcount > 0:
                    inserted += 1
                else:
                    skipped_duplicate += 1
            except Exception as e:
                print(f"    [ERROR] ({source_id}->{target_id} {rel_type}): {e}")
        db.commit()
        print(f"\nInserted: {inserted} | Duplicates skipped: {skipped_duplicate}")
    else:
        print("\n[DRY RUN] No changes written.")

    print(f"Stats: no_rel_section={skipped_no_rel_section} | unresolved_source={skipped_unresolved_source} | unresolved_target={skipped_unresolved_target}")
    db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed entity_relationships from vault stubs")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    seed_relations(dry_run=args.dry_run)
