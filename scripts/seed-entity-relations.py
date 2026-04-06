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
import json
import logging
import re
import sqlite3
import sys
from pathlib import Path
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

sys.path.insert(0, "/data/tools/qmd-azure-embed")

from mnemosyne._azure import chat_completion
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

# Canonical relationship types accepted by the LLM classifier
VALID_REL_TYPES = {
    "FOUNDED_BY", "OWNS", "WORKS_AT", "PARTNER_OF", "RELATED_TO",
    "CLIENT_OF", "EMPLOYED_BY", "LED_BY", "CREATED_BY", "CONTRIBUTES_TO",
}

# Map legacy pattern-classifier labels to the canonical set
_LEGACY_MAP = {
    "OWNED_BY":    "OWNS",
    "LEADS":       "LED_BY",
    "INVESTED_BY": "RELATED_TO",
}


def _pattern_classify(context_text: str) -> str:
    """Regex-based relationship classifier (original logic)."""
    for pattern, rel_type in RELATIONSHIP_PATTERNS:
        if pattern.search(context_text):
            return rel_type
    return "RELATED_TO"


def _llm_classify_batch(items: list) -> list:
    """
    Classify a batch of (target_name, context_line) pairs using GPT-4o-mini.

    Args:
        items: list of (target_name: str, context_line: str) tuples, up to 10.

    Returns:
        List of relationship type strings in the same order as items.
        Falls back to _pattern_classify() per item on any exception.
        Never raises.
    """
    if not items:
        return []

    valid_types_str = ", ".join(sorted(VALID_REL_TYPES))
    numbered_lines = []
    for i, (name, line) in enumerate(items):
        numbered_lines.append(
            str(i + 1) + ". target=" + repr(name) + " context=" + repr(line.strip()[:120])
        )
    numbered = "\n".join(numbered_lines)

    system_msg = (
        "You are a knowledge-graph relationship classifier. "
        "Given a list of entity relationships, classify each one into exactly one of these types: "
        + valid_types_str + ". "
        "Reply with ONLY a JSON array of strings, one per input item, in the same order. "
        'Example: ["FOUNDED_BY", "WORKS_AT"]. No explanation.'
    )
    user_msg = "Classify these relationships:\n" + numbered

    try:
        raw = chat_completion(
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user",   "content": user_msg},
            ],
            max_tokens=200,
        )
        if not raw:
            raise ValueError("empty response from chat_completion")

        # Strip markdown code fences if present
        cleaned = re.sub(r"```[\w]*\n?", "", raw).strip().strip("`").strip()
        parsed = json.loads(cleaned)
        if not isinstance(parsed, list) or len(parsed) != len(items):
            raise ValueError("unexpected response shape: " + repr(parsed))

        results = []
        for val in parsed:
            val_upper = str(val).strip().upper()
            if val_upper not in VALID_REL_TYPES:
                logger.warning("_llm_classify_batch: unknown type %r — using RELATED_TO", val_upper)
                val_upper = "RELATED_TO"
            results.append(val_upper)
        logger.info("_llm_classify_batch: classified %d items via LLM", len(items))
        return results

    except Exception as e:
        logger.warning("_llm_classify_batch: error %s — falling back to pattern classifier", e)
        return [
            _LEGACY_MAP.get(_pattern_classify(line), _pattern_classify(line))
            for _, line in items
        ]


def classify_relationship(context_text: str) -> str:
    """
    Single-item relationship classifier.
    Uses the pattern classifier and maps legacy labels to the canonical set.
    Kept for backwards compatibility and as the batch fallback.
    """
    raw = _pattern_classify(context_text)
    return _LEGACY_MAP.get(raw, raw)


def extract_entity_name_from_path(stub_path) -> str:
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

    BATCH_SIZE = 10

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

        # Resolve targets first so we only classify resolvable pairs
        resolved_pairs = []  # list of (target_id, target_name, context_line)
        for target_name, context_line in rels:
            target_id = resolve_entity(target_name, db)
            if target_id is None:
                print(f"    [SKIP] Unresolved target: {target_name!r}")
                skipped_unresolved_target += 1
                continue
            resolved_pairs.append((target_id, target_name, context_line))

        # LLM-classify in batches of up to BATCH_SIZE (minimises API calls)
        for batch_start in range(0, len(resolved_pairs), BATCH_SIZE):
            batch = resolved_pairs[batch_start : batch_start + BATCH_SIZE]
            batch_items = [(name, line) for _, name, line in batch]
            try:
                rel_types = _llm_classify_batch(batch_items)
            except Exception as e:
                logger.warning("LLM batch classify failed (%s) — using pattern fallback", e)
                rel_types = [classify_relationship(line) for _, line in batch_items]

            for (target_id, target_name, context_line), rel_type in zip(batch, rel_types):
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
