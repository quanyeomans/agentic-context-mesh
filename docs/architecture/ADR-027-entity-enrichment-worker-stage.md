# ADR-027 — Entity-enrichment worker stage (post-iter_5 deployment)

**Status:** Proposed
**Date:** 2026-05-29
**Supersedes:** none
**Superseded by:** none
**Tracking:** GH #343 (one-time iter_5 deployment); follow-up wave for the worker integration
**Source plan:** entity-modelling repo, `docs/kairix-deployment-plan.md` §11
**Norm reference:** entity-modelling repo, `docs/graph-modelling-external-refs-as-properties.md` (ported in same wave as `docs/architecture/graph-modelling-refs-as-properties.md`)

## Context

The iter_5 entity-modelling pipeline (Wikidata + Perplexity enrichment + entity resolution + corpus co-occurrence) ships as a one-time cypher-shell deployment into production Neo4j (GH #343). That deployment lifts the graph from 871 nodes / 2 rel types → ~27,000 nodes / 12 rel types.

The deployment is **static**: it loads a snapshot of the corpus at one point in time. Going forward, as kairix ingests new documents (SharePoint, Notion, Slack, etc.) and extracts new entity signals via the silver layer, the enrichment doesn't re-fire. New documents flow through:

- `kairix.knowledge.store.crawler` — vault frontmatter → curated entities (rich)
- `kairix.knowledge.entities.seed.seed_graph` — regex auto-discovery → minimal entities (the §10.7 patch in GH #343 already prevents this from regenerating cleansed canonical entities)
- `kairix.core.curator.drain.run_default_drain_tick` — silver layer `entity_signals` → `:Person` / `:Organisation` MERGE with `{last_seen_at, confidence, source_uri}` only

None of these enrich. New entities surfaced after the iter_5 load remain unenriched against Wikidata / Perplexity until either:

1. A new one-off iter_N pipeline runs externally and re-deploys (operationally heavy — full vault re-process + cypher-shell + cleanse cycle)
2. The enrichment becomes a kairix worker stage that runs on every index pass (this ADR)

This ADR proposes (2) — converting the entity-modelling pipeline into a first-class kairix worker stage so enrichment is **continuous**, not periodic.

## Decision

Add a new worker tick `_run_entity_enrichment` that fires after the embed pass (every ~24h cadence) and runs three stages against the engagement-scope SQLite DB + production Neo4j:

1. **Enrich** — newly-seeded entity rows in the `entities` SQLite table (placed by `seed.py` or surfaced from `curator/drain.py`) get a Wikidata candidate-lookup, fall back to Perplexity for long-tail, write the result into a new `entities_enrichment` SQLite table.
2. **Resolve** — entities mapped to the same canonical Wikidata QID get merged via a `entities_canonical_map` table.
3. **Promote** — confirmed high-confidence (≥0.85) enrichments get MERGE'd into Neo4j against their canonical kairix slug; below-threshold candidates land in a `:Candidate` review queue for operator triage.

### Module layout

```
kairix/knowledge/entities/
├── enrich.py        (NEW)    wraps entity-modelling/enrich.py + perplexity_enrich.py
├── resolve.py       (NEW)    wraps entity-modelling/resolve_entities.py
├── promote.py       (NEW)    promotes resolved entities → Neo4j MERGE
├── candidates.py    (NEW)    queue + review surface
├── seed.py          (PATCHED §10.7 in GH #343 — skip canonical slugs)
├── suggest.py       (PATCHED — see §3.3)
└── cli.py           (EXTENDED — kairix entities review subcommand)

kairix/core/db/schema.py
  + entities_enrichment    (NEW table)
  + entities_canonical_map (NEW table)
  + entities_review_queue  (NEW table)

kairix/worker.py
  + _run_entity_enrichment   (NEW tick)
  + EntityEnrichmentDeps     (NEW dataclass — DI seam)
```

### Schema additions

```sql
CREATE TABLE IF NOT EXISTS entities_enrichment (
    entity_id TEXT PRIMARY KEY,            -- kairix slug
    wikidata_qid TEXT,
    wd_label TEXT,
    wd_description TEXT,
    wikipedia_url TEXT,
    confidence REAL NOT NULL,
    enrichment_status TEXT NOT NULL,        -- 'matched' | 'pplx-only' | 'unmatched'
    match_score REAL,
    enriched_at INTEGER NOT NULL,
    source TEXT NOT NULL,                   -- 'wikidata' | 'perplexity' | 'local'
    promoted_to_neo4j INTEGER DEFAULT 0     -- F67 staging-drain pair
);

CREATE TABLE IF NOT EXISTS entities_canonical_map (
    surface_form TEXT PRIMARY KEY,         -- as observed in corpus
    canonical_id TEXT NOT NULL,             -- kairix slug
    resolution_method TEXT NOT NULL,        -- 'qid' | 'wd_label' | 'synonym' | 'fuzzy'
    resolved_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS entities_review_queue (
    candidate_id TEXT PRIMARY KEY,         -- kairix slug
    suggested_class TEXT NOT NULL,
    name TEXT NOT NULL,
    confidence REAL NOT NULL,
    source_docs TEXT,                       -- JSON array
    queued_at INTEGER NOT NULL,
    reviewed_at INTEGER,
    decision TEXT,                          -- 'promote' | 'reject' | 'merge-to:<id>'
    reviewed_by TEXT
);
```

`entities_enrichment.promoted_to_neo4j` is a deliberate F67 (staging-drain symmetry) pairing — every row flips 0 → 1 on successful Neo4j MERGE by the promote step.

### Worker tick shape

```python
@dataclass(frozen=True)
class EntityEnrichmentDeps:
    """DI seam for _run_entity_enrichment (F6-clean — real defaults, no None)."""
    db_factory: Callable[[], sqlite3.Connection] = field(default=_default_open_db)
    neo4j_client_factory: Callable[[], Neo4jClient] = field(default=_default_get_client)
    wikidata_lookup: Callable[[str], dict[str, Any] | None] = field(default=wikidata_search)
    perplexity_lookup: Callable[[str], dict[str, Any] | None] = field(default=perplexity_search)
    confidence_threshold: float = 0.85
    per_tick_max_items: int = 200  # F66
    disk_watermark_min_free_bytes: int | None = 1_000_000_000  # F66

def _run_entity_enrichment(deps: EntityEnrichmentDeps | None = None) -> EntityEnrichmentResult:
    """Per-tick enrichment pass — F66 budget-bounded, F67 drain-symmetric."""
    ...
```

### Forward-going graph-write topology

After this ADR lands, three concurrent writers continue + one new:

| Writer | Status | Output |
|---|---|---|
| `crawler.py` (ADR-014) | unchanged | curated entities from vault frontmatter |
| `seed.py` (regex auto-discovery) | already patched in #343 | minimal entities NOT in canonical map |
| `curator/drain.py` (entity_signals → Neo4j) | unchanged | silver-layer entity signal MERGE |
| `_run_entity_enrichment` (NEW) | this ADR | continuous Wikidata + Perplexity enrichment of newly-seeded entities |

The §11.3 vision goes further: `suggest.py` would be patched so that **all new entities flow through the candidate queue instead of direct Neo4j writes**. That's a behaviour change (today `suggest_entities` writes immediately if confidence > threshold; after the patch it would queue). Defer that change to a follow-up ADR — for the initial ADR-027 cut, keep `suggest.py` behaviour unchanged and run enrichment as an additive layer.

## Mechanics

### 3.1 Enrich step

For each entity in `entities` SQLite table NOT present in `entities_enrichment`:

1. Wikidata candidate search via `wikidata_lookup(entity.name)`.
2. If match_score ≥ 0.85 → write to `entities_enrichment` with `source='wikidata'`, `status='matched'`.
3. Else → Perplexity fallback `perplexity_lookup(entity.name)`.
4. If Perplexity match ≥ 0.65 → write `source='perplexity'`, `status='pplx-only'`.
5. Else → write `source='local'`, `status='unmatched'`.

F66-bounded: stop at `per_tick_max_items=200` per tick.

### 3.2 Resolve step

For each new row in `entities_enrichment` with `wikidata_qid IS NOT NULL`:

1. Look up `entities` rows whose name (or alias) matches the canonical Wikidata label or any Wikidata alias.
2. For each matching entity_id != the canonical id, write `entities_canonical_map(surface_form, canonical_id, 'qid|wd_label|synonym|fuzzy', now)`.
3. After resolve, the canonical kairix slug is the single ID downstream code uses (see seed.py patch GH #343 §10.7).

### 3.3 Promote step

For each row in `entities_enrichment` with `promoted_to_neo4j = 0` AND `confidence ≥ 0.85`:

1. MERGE the corresponding node in Neo4j keyed on `entity_id` (kairix slug):
   - `MERGE (n:<Label> {id: entity_id}) SET n.wikidata_qid = ..., n.description = ..., n.enrichment_confidence = ..., n.kairix_provenance_batch = 'continuous-<YYYY-MM>'`
2. Augment properties non-destructively (curated wins — same rule as the one-time deployment).
3. Mark `entities_enrichment.promoted_to_neo4j = 1`.

Below-threshold (< 0.85) rows go to `entities_review_queue` instead; operator reviews via `kairix entities review` CLI.

### 3.4 Operator review surface

```bash
$ kairix entities review --list
# 12 candidates pending review:
#   acme-corp        | Organisation | 0.62 | matched 3 docs
#   john-smith       | Person       | 0.58 | matched 1 doc
#   ...

$ kairix entities review acme-corp --decision promote
# promotes acme-corp from review_queue → Neo4j MERGE

$ kairix entities review acme-corp --decision merge-to:acme-inc
# folds acme-corp into existing acme-inc canonical node
```

MCP surface (parallel to F53 pattern):

```python
@server.tool()
def tool_entities_review(...) -> dict:
    """List + promote/reject candidates in the entity-review queue."""
    ...
```

### 3.5 Drift monitoring

Weekly job (separate from the worker tick) writes a summary to the worker log:

```
entity_enrichment_drift: class_distribution_delta_pct={
  organisation: +12.3, person: -2.1, place: +0.5, ...
}
```

Alert (worker log WARNING level) if any class delta > 20%. Indicates extractor regression upstream — operator investigates.

## kairix fitness functions this work will trip

Per my review of the §11 plan, the worker integration will trigger several existing F-rules. Each becomes part of the implementation acceptance:

| Rule | Why it fires | Acceptance |
|---|---|---|
| **F66** (per-tick budget + watermark) | `_run_entity_enrichment` is a tick-driven component | Declare `per_tick_max_items` + `disk_watermark_min_free_bytes` on the deps dataclass + class |
| **F67** (staging-drain symmetry) | `entities_enrichment.promoted_to_neo4j` column requires UPDATE flipping it 0 → 1 | Wire the promote step to do the flip; F67 sees the symmetry |
| **F70** (schema-writer symmetry) | New `entities_enrichment`, `entities_canonical_map`, `entities_review_queue` tables | Each gets INSERT sites in the enrich / resolve / queue modules |
| **F45** (new-capability BDD) | `kairix entities review` CLI subcommand | Add `tests/bdd/features/cli_entities_review.feature` in the same commit |
| **F30** (operator-outcome tests) | Same CLI + new MCP `tool_entities_review` | Add outcome tests asserting stdout / returned envelope content |
| **F53** (status surface) | Operator-facing review queue | Add `kairix entities status` summary subcommand mirroring `kairix features status` |
| **F54** (flag both-branch) | If gated behind a feature flag | OFF + ON BDD scenarios + integration tests for `entity_enrichment_continuous` flag |
| **F68** (Protocol failure modes) | New Protocol surface for `WikidataLookup`, `PerplexityLookup` | Failure-mode contract tests under `tests/contracts/test_<protocol_snake>_failure_modes.py` |
| **F64** (HTTP rate-limit) | Wikidata + Perplexity are HTTP clients | Rate-limit + Retry-After tests under `tests/integration/` |
| **F77** (single-writer SQLite) | New tables in `entities_*` family — schema bootstrap is in factory | All writes via the worker's coordinator — no new sqlite3.connect call sites |

## Definition of done

| # | Criterion | Verification |
|---|---|---|
| 1 | iter_5 cypher-shell deployment landed in production (GH #343 phases 1-4 complete) | Verify queries §9.1–9.6 green |
| 2 | `kairix.knowledge.entities.{enrich,resolve,promote,candidates}` modules exist | Import + unit test coverage |
| 3 | Three new SQLite tables in schema.py + writers exist | F70 baseline shrinks |
| 4 | `_run_entity_enrichment` worker tick wired with F66 budget declarations | F66 green; tick runs without OOM on production-scale entity table |
| 5 | F67 `promoted_to_neo4j` flip implemented | F67 sees the UPDATE site; drain backlog trends to zero |
| 6 | `kairix entities review` CLI + `tool_entities_review` MCP + F45/F30/F53 tests | All four green |
| 7 | Both-branch tests for any new feature flag (e.g. `entity_enrichment_continuous`) | F54 green |
| 8 | Wikidata + Perplexity Protocol failure-mode tests (F68) + rate-limit tests (F64) | Both green; baselines reduced |
| 9 | Drift monitoring writes weekly summary to worker log | Operator-attested after first weekly cycle |
| 10 | `docs/architecture/graph-modelling-refs-as-properties.md` ported from entity-modelling repo as kairix norm | Document exists; ADR-027 references it |

## Open decisions for later

1. **Wikidata vs Perplexity ordering** — Wikidata first (free, deterministic) is recommended. Worth testing if Perplexity-first improves long-tail recall.
2. **Auto-promote threshold** — 0.85 is the entity-modelling pilot's choice. May need per-class tuning (Person and Place tend toward more confident matches; Vocation less so).
3. **Review queue retention** — how long do unreviewed candidates stay queued before auto-prune? Recommend 90 days, then archive to a `entities_review_archive` table (or delete with provenance).
4. **`suggest.py` patch** — defer or include in this ADR? Recommend defer to ADR-028 (or follow-up section) because it's a behaviour change for an existing MCP surface (`kairix.use_cases.entity.run_entity_suggest`).
5. **Feature flag** — gate the whole tick behind `entity_enrichment_continuous` (FlagGatedCapability per ADR-026 Track C)? Pro: safe cutover, F54 enforced; con: another flag to retire. Recommend yes, with `target_retire_in` 6 months after stable rollout.

## Sequencing

1. **First (this session)**: GH #343 phases 0a/0b/0c/0d/0e + this ADR + cypher-shell deployment artefacts
2. **After GH #343 phase 4 complete (operator runs deploy)**: write the schema migration + module skeletons for ADR-027
3. **Within 1 month of deployment**: ship the `_run_entity_enrichment` tick + review CLI
4. **Within 3 months**: drift-monitoring weekly summary live
5. **Within 6 months**: evaluate whether to ship the §11.3 `suggest.py` patch (queue-instead-of-write) under ADR-028
