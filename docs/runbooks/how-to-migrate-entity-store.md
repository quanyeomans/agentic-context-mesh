# How-To: Migrate Entity Store from SQLite to Neo4j

## Context

Prior to kairix v0.9, entity nodes and relationships were stored in a SQLite
database (`entities.db`) alongside the sqlite-vec search index. This pattern
created two sources of truth and a fragile manual sync step.

**From v0.9.2 onward, Neo4j is the sole canonical entity store.** The sqlite-vec
database (`kairix.db` / the QMD index) remains in place — it holds the BM25 and
vector search index, which is a separate concern.

---

## What Changed

| Concern | Old location | Current location |
|---------|-------------|-----------------|
| Entity nodes (Organisation, Person, Outcome…) | `entities.db` → `entities` table | Neo4j — labelled nodes |
| Entity relationships | `entities.db` → `entity_relationships` table | Neo4j — typed edges (`RELATED_TO`, `PARTNER_OF`…) |
| BM25 + vector search index | `entities.db` (co-located, confusing) | `kairix.db` / QMD sqlite-vec index (unchanged) |
| Curator health check | `run_health_check(db, neo4j_client=None)` | `run_health_check(neo4j_client=client)` |
| Entity prune script | `prune-entities.py` (SQLite) | `prune-entities.py` (Neo4j — same file, rewritten) |
| Relationship seeder | `seed-entity-relations.py` (INSERT INTO entity_relationships) | `seed-entity-relations.py` (Neo4j MERGE edges) |

---

## If You Have an Existing `entities.db`

The file is safe to leave in place — kairix no longer reads from or writes to it.
When you are confident the Neo4j graph contains all data you need, you may
archive or delete it:

```bash
# Archive (recommended)
mv /data/mnemosyne/entities.db /data/mnemosyne/entities.db.retired-$(date +%Y%m%d)

# Or delete when satisfied
rm /data/mnemosyne/entities.db
```

---

## Seeding Entities into Neo4j

If you have an existing `entities.db` and want to migrate its content to Neo4j,
use the curator pipeline:

```bash
# 1. Verify Neo4j is reachable
kairix curator health

# 2. Re-seed entity nodes from vault stubs (stubs are the source of truth)
kairix curator seed --vault-root $KAIRIX_VAULT_ROOT

# 3. Re-seed relationships from vault stub wikilinks
python scripts/seed-entity-relations.py --dry-run   # preview
python scripts/seed-entity-relations.py             # apply

# 4. Verify graph health
kairix curator health
```

---

## Verifying the Migration

```bash
kairix curator health
```

Expected output on a healthy graph:
```
## Entity Counts (total: N)
| Type         | Count |
|--------------|-------|
| organisation | ...   |
| person       | ...   |

## Synthesis Failures (0)
✅ None.

## Stale Entities (0, threshold: 90 days)
✅ None.

## Missing Vault Path (0)
✅ None.

## Graph (Neo4j)
✅ Connected — N Organisation, M Person, ...

Status: ✅ HEALTHY — no issues found
```

---

## Environment Variables

| Variable | Description |
|----------|-------------|
| `KAIRIX_NEO4J_URI` | Neo4j Bolt URI (default: `bolt://localhost:7687`) |
| `KAIRIX_NEO4J_USER` | Neo4j username (default: `neo4j`) |
| `KAIRIX_NEO4J_PASSWORD` | Neo4j password — **required** |
| `KAIRIX_VAULT_ROOT` | Obsidian vault root (default: `/data/obsidian-vault`) |

---

## Code References Removed in v0.9.2

The following were removed or rewritten as part of this migration:

- `kairix.entities.schema.open_entities_db()` — removed; use `kairix.graph.client.get_client()`
- `kairix.entities.graph.entity_lookup()` — removed; use `Neo4jClient.find_by_name()`
- `run_health_check(db, neo4j_client=None)` → `run_health_check(neo4j_client=client)`
- `MNEMOSYNE_ENTITIES_DB` env var — retired; no replacement needed
- `MNEMOSYNE_VAULT_ROOT` env var — replaced by `KAIRIX_VAULT_ROOT`
- `MNEMOSYNE_E2E=1` env var — replaced by `KAIRIX_E2E=1`
