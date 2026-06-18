# ADR-036 — Entity-summary indexing surface

**Status:** Proposed (2026-06-09)
**Issue:** [#457](https://github.com/three-cubes/kairix/issues/457) — design sub-issue of [#429](https://github.com/three-cubes/kairix/issues/429)
**Related:** EPIC [#438](https://github.com/three-cubes/kairix/issues/438), ADR-027 (entity-enrichment worker stage)

## Context

`kairix.knowledge.entities.enrich.enrich_entity` writes Wikidata
descriptions into `Neo4j.n.summary` for ~7,461 entity nodes today
(2026-06-08 enrichment run). Those summaries are reachable by direct
`tool_entity` / `kairix entity` lookup and by opt-in `--include-entity-card`
augmentation — but they do NOT participate in first-pass BM25 / vector
retrieval. Net effect on the 2026-06-08 reflib eval: entity-category
NDCG = 0.380 (vs 0.6+ for other categories), and an agent asking "which
AI-ethics organisations did we engage with" misses entities whose only
relevance signal is in the unindexed `n.summary`.

#429 (the parent issue) frames the problem; this ADR locks the
architecture so an implementation PR can land cleanly. Three constraints
shape the design space:

1. **No coupling** between enrichment and retrieval write paths.
   `enrich_entity` runs at firm scope (no DB write permission); the
   retrieval index lives in the worker's SQLite. Today they share
   nothing; that boundary must stay.
2. **EPIC #438 compatibility.** The source-tier model (#432) and the
   fact-layer floor (#455) must keep working. Entity summaries need a
   tier assignment that fits the matrix.
3. **F44 + F77 + F50 stay clean.** Worker owns SQLite writes; new
   files don't add baseline entries.

## Decision

A new worker-tick stage **`EntitySummaryProjectorStage`** polls Neo4j
for entities whose `n.summary` is populated but unindexed, projects each
into one chunk in a synthetic **`entity-summaries` collection** (tier
**`reference`**) via the canonical `_SqliteChunkWriter`, and marks each
entity `n.summary_indexed_at = $now` in Neo4j on success. The existing
embed worker picks up the new chunks on its next tick — no notification
plumbing required. The feature is gated by
**`entity_summary_indexing_enabled`** (default OFF) so the cutover is
default-safe per the feature-flag architecture.

Re-running enrichment with an unchanged summary is a no-op (idempotent
via `content_hash`); a CHANGED summary deletes the prior chunk for
`source_uri="entity://<QID>"` and re-projects + re-embeds. Search
results carry the existing envelope shape; renderers (CLI + MCP)
optionally badge `source_uri.startswith("entity://")` rows with
`[Wikidata]`. No new search-pipeline plumbing required.

### Why this shape

| Question | Choice | Why |
|---|---|---|
| **Q1: chunk-store location** | Synthetic collection `entity-summaries` | Existing collection plumbing (BM25 / vec / dedup / tier / boost) flows for free. Operators see it as a regular collection they can include/exclude via scope. No new search backend. |
| **Q2: write path** | Worker-tick projector | Eventual-consistency is appropriate (entities don't change minute-by-minute). Failure isolation: Wikidata fetch and Neo4j write are unchanged; chunk-write lives in the worker where SQLite-write permission already lives (F44/F77 clean). `enrich_entity` keeps its firm-scope no-write-permission posture. |
| **Q3: embed-worker notify** | None | Chunks land in the same `chunks` table the embed worker already polls. The existing tick picks them up. |
| **Q4: source-tier (#438)** | `reference` (x0.6 default) | Wikidata is external context, not operator canon. Reference-tier means entity rows appear in results but vault canonical content wins on tie. Operators can re-tier via `kairix.config.yaml` if they want entity summaries to outrank. |
| **Q5: FTS5 index** | Same index as document chunks | Free with Q1. The FTS5 trigger is at the chunk-table level; no second index. |
| **Q6: idempotency + re-write** | Stable `source_uri="entity://<QID>"`; projector deletes-prior-then-writes on summary change — requires extending the `ChunkWriter` Protocol with `delete_by_source_uri(uri) -> int` | Re-running yields same `content_hash` → skip-embed via cache. Summary-changed → delete + new content_hash → re-embed. Same pattern as connector re-ingest. The Protocol extension is one-line + needs `_SqliteChunkWriter` + `_CollectionRouterChunkWriter` + any fake to gain the method (see Slice B). |
| **Q7: presentation** | Existing envelope + optional `[Wikidata]` badge keyed on `source_uri` prefix | Zero schema change. Renderers gate on the well-known `entity://` URI prefix. |

### Module layout

```
kairix/
  knowledge/
    entities/
      summary_projector.py     # NEW — EntitySummaryProjector + Stage adapter
  worker.py                    # MODIFIED — register the new stage in the tick chain
  core/
    features/
      registry.py              # MODIFIED — declare entity_summary_indexing_enabled
docs/
  architecture/
    ADR-036-entity-summary-indexing-surface.md     # THIS DOC
tests/
  bdd/features/
    entity_summary_indexing.feature                # NEW — F45 capability coverage
    feature_flag_entity_summary_indexing_enabled.feature
  bdd/steps/
    entity_summary_indexing_steps.py
    feature_flag_entity_summary_indexing_enabled_steps.py
  bdd/
    test_entity_summary_indexing.py
    test_feature_flag_entity_summary_indexing_enabled.py
  contracts/
    test_entity_summary_projector_protocol.py      # NEW — F43 contract proof
  integration/
    test_entity_summary_projector_lifecycle.py     # F47 — canonical factory shape
    test_feature_flag_entity_summary_indexing_enabled.py  # F54 both-branch
  e2e/
    test_composed_entity_summary_path.py           # F48 — full composed E2E
  unit/
    test_entity_summary_projector.py               # signal helpers + chunk builder
```

### Protocol

```python
# kairix/core/protocols.py — add to the boundary list
@dataclass(frozen=True)
class EntitySummaryProjectionResult:
    """Outcome of one EntitySummaryProjector.tick() call.

    Surfaced so worker telemetry and operator-facing diagnostics can
    show progress without re-querying Neo4j.
    """
    projected: int      # net-new chunks written this tick
    updated: int        # existing chunks deleted-and-rewritten this tick
    skipped: int        # entities polled but no work (already indexed + unchanged)
    failed: int         # per-entity write failures (logged, swallowed)


@runtime_checkable
class EntitySummaryProjector(Protocol):
    """Projects Neo4j entity summaries into the chunk store.

    Reads :class:`Neo4jClient`, writes via :class:`ChunkWriter` (the
    routing flows via the canonical CollectionRouter →
    _SqliteChunkWriter path so F61 stays clean).
    """
    def tick(
        self,
        *,
        per_tick_max_items: int = 200,   # F66 contract
    ) -> EntitySummaryProjectionResult: ...
```

### Worker tick shape

```python
# kairix/worker.py — pseudo
class Worker:
    def tick(self) -> WorkerTickResult:
        # ... existing stages ...
        if flag("entity_summary_indexing_enabled"):
            result = self._entity_summary_projector.tick(
                per_tick_max_items=self._cfg.entity_summary_per_tick_max,
            )
            self._telemetry.record_entity_summary_projection(result)
        # ... existing embed stage ...
```

`per_tick_max_items` defaults to **200** so a 7,461-entity backlog
clears in ~38 ticks (worker ticks every ~30s by default, so backlog
clears in ~19 min on the first cutover). F66-compliant
(`per_tick_max_items` + `disk_watermark_min_free_bytes` both declared).

### Schema additions

**Neo4j (per-entity):**
```cypher
SET n.summary_indexed_at = $now            // ISO-8601 UTC
SET n.summary_indexed_content_hash = $hash // sha256 of summary text
```
Re-projection trigger: `summary != null AND (summary_indexed_at IS NULL
OR summary_indexed_content_hash != sha256(summary))`.

**SQLite:** none. `_SqliteChunkWriter` routes to the existing `chunks`
table via the standard collection (`entity-summaries`). FTS5 trigger
indexes automatically. `vec_index` picks up via the existing embed-tick.

**`kairix.config.yaml`** — operator declares the synthetic collection's
tier (F61-clean, same surface as any other collection):
```yaml
collections:
  shared:
    - name: entity-summaries
      tier: reference
```

## Mechanics

### Tick step — find candidates

```cypher
MATCH (n)
WHERE n.summary IS NOT NULL
  AND n.summary <> ''
  AND (n.summary_indexed_at IS NULL
       OR coalesce(n.summary_indexed_content_hash, '') <> '')
RETURN n.name AS name,
       n.wikidata_qid AS qid,
       n.summary AS summary,
       n.summary_indexed_content_hash AS prior_hash,
       n.summary_source AS summary_source
LIMIT $per_tick_max_items
```

Python computes `current_hash = sha256(summary)` for each returned row,
then filters in Python: row qualifies iff `prior_hash is None or
prior_hash != current_hash`. Cypher pre-filters away the trivially-stale
rows (already-indexed-and-still-empty-hash case is impossible — empty
hash means never-indexed) but the authoritative comparison happens
Python-side so no APOC dependency is introduced. Trade-off: when no
entity has been edited, the Cypher returns rows the Python loop will
skip — same row count as the current Neo4j enrichment polling pattern
already pays.

### Tick step — write chunks

For each candidate:
```python
chunk = Chunk(
    text=summary,                                # the description
    content_hash=sha256(summary).hexdigest(),
    source_name="wikidata",
    source_uri=f"entity://{qid}",                # idempotency key
    source_modified_at=tick_started_at_iso,      # ISO-8601 UTC of the projector tick
    source_page=None,
    sensitivity=Sensitivity.PUBLIC,              # Wikidata is public — see F39 note below
    chunker_version="entity-summary:v1",         # F55 — chunker registry namespace
    author=None,
    author_email=None,
    tags=("entity-summary", f"qid:{qid}"),
    metadata={"entity_name": name, "wikidata_qid": qid},
)
```

**Why `tick_started_at`, not an enrichment timestamp.** `enrich_entity`
writes `n.summary` and `n.summary_source` only — it does NOT today set
a `n.summary_set_at` field. The projector therefore can't read a real
"summary modified at" timestamp from Neo4j. Using the tick-start ISO is
adequate for `source_modified_at`'s purpose (downstream temporal-boost
ordering): once the chunk lands in the index it stays there until the
summary changes, at which point delete-then-rewrite stamps the new
tick-start. A future enhancement (out of scope for #457) would have
`enrich_entity` SET `n.summary_set_at = $now` so the projector can
forward that real timestamp.

**F39 sensitivity-PUBLIC compliance.** F39 requires `Sensitivity.PUBLIC`
to be valid only when "the connector config declares the public tier
explicitly." Entity-summary chunks have no connector config. Two
resolutions live in Slice A:

1. **Add a synthetic connector-config entry** for `wikidata` declaring
   `default_sensitivity: public`. The connector framework's config
   loader already supports synthetic / no-poll entries (see
   `connector-ingestion-architecture.md` §"opaque sources"). This keeps
   F39 mechanically enforced via the same loader path everyone else
   uses.
2. **Exempt the projector from F39** with a per-line `# F39-exempt:
   wikidata is intrinsically public; no operator config required` and
   a rationale row in `.architecture/baseline/f39-files.txt`.

Recommend (1) — the synthetic config entry is one YAML block and keeps
the fitness rule honest. Operators who later want to redact / opt-out
of Wikidata content can flip the synthetic connector's sensitivity in
their overlay.
The projector calls `chunk_writer.delete_by_source_uri(uri)` first when
`prior_hash != null` (re-projection case). Net write: 1 chunk per
entity, every time.

### Tick step — mark indexed

After successful chunk-write:
```cypher
MATCH (n {name: $name})
SET n.summary_indexed_at = $now,
    n.summary_indexed_content_hash = $hash
```

Failure isolation: Neo4j mark-indexed runs in the same try-block as the
chunk write. If mark-indexed fails (degraded Neo4j), the chunk stays
written (idempotent next tick via content_hash) but the entity will
re-project until Neo4j recovers. Logged at WARN.

## Test discipline (test-discipline-hardening §F45..F49 + F54)

Every shippable slice must carry the matching test row:

| Layer | What | Where | Gate |
|---|---|---|---|
| Unit | chunk-builder, hash compute, change-detection logic | `tests/unit/test_entity_summary_projector.py` | F8 unit marker |
| Contract | `EntitySummaryProjector` Protocol + fake + failure modes | `tests/contracts/test_entity_summary_projector_protocol.py` | F43 contract |
| Integration | Full lifecycle: seed Neo4j summary → projector.tick() → assert chunk in SQLite + FTS5 row | `tests/integration/test_entity_summary_projector_lifecycle.py` | F47 factory shape |
| Integration (flag) | OFF-branch + ON-branch via `with_flag` | `tests/integration/test_feature_flag_entity_summary_indexing_enabled.py` | F54 both-branch |
| BDD | Operator runs enrichment → next search finds the description | `tests/bdd/features/entity_summary_indexing.feature` + steps | F45 capability + F12 happy-path |
| BDD (flag) | OFF + ON scenarios | `tests/bdd/features/feature_flag_entity_summary_indexing_enabled.feature` | F54 |
| E2E | Composed-path: enrich → tick → embed → search → assertion | `tests/e2e/test_composed_entity_summary_path.py` + `@pytest.mark.e2e` | F48 |

### Failure-injection contract tests (F68 per Protocol method)

```python
@pytest.mark.contract
def test_projector_tick_handles_neo4j_unavailable(fake_neo4j_unavailable, fake_chunk_writer):
    """Neo4j unavailable → projector returns failed=0, projected=0; never raises."""

@pytest.mark.contract
def test_projector_tick_handles_chunk_writer_raises(fake_neo4j, raising_chunk_writer):
    """ChunkWriter raises on upsert → projector logs WARN + failed=1; continues."""

@pytest.mark.contract
def test_projector_tick_handles_partial_failures(fake_neo4j_with_5_entities, flaky_chunk_writer):
    """3 succeed + 2 fail → projector returns projected=3, failed=2."""

@pytest.mark.contract
def test_projector_tick_respects_per_tick_max_items(fake_neo4j_with_500_entities, fake_chunk_writer):
    """per_tick_max_items=100 caps work; remainder picked up next tick."""

@pytest.mark.contract
def test_projector_tick_idempotent_on_unchanged_summary(fake_neo4j_with_indexed_entity, fake_chunk_writer):
    """Same summary as prior tick → skipped=1, projected=0, no chunk-writer call."""
```

### F69 scale-bound test (≥10K rows)

```python
@pytest.mark.soak
def test_projector_clears_10k_entity_backlog():
    """Seed 10K entities with summaries; assert projector clears in ≤50 ticks
    and ends with 10K chunks in the entity-summaries collection."""
```

### Sabotage-proof checklist per test (per `feedback_sabotage_must_be_executed`)

For each new test:
1. Mutate production: comment out the projector's `chunk_writer.upsert` call
2. Run test → confirm it fails
3. Restore + confirm it passes again
4. Log the mutate→fail→restore in the commit body

## Expected behaviours (acceptance contract)

1. **Default-safe.** With `entity_summary_indexing_enabled = False`,
   the projector stage is a no-op (zero Neo4j queries, zero chunks
   written). Pre-#457 behaviour preserved byte-for-byte.
2. **First-cutover happy path.** Flag ON + worker tick:
   `summary_indexed_at IS NULL` entities project at
   `per_tick_max_items` per tick until the backlog clears.
3. **Search match.** Query "AI policy research institute" returns the
   `entity-summaries`-collection chunk for `Q12345` when Q12345's
   summary contains those words. The chunk appears in fused results
   with `source_uri=entity://Q12345`, ranked according to its
   `reference` tier (x0.6 default).
4. **Idempotency.** Running `worker.tick()` twice in a row with no
   Neo4j changes between ticks projects zero new chunks on the second
   call. `summary_indexed_at` set on first tick, hash check skips on
   second.
5. **Update propagation.** Operator re-runs `enrich_entity(..., overwrite=True)`
   for an entity whose Wikidata description changed. Next worker tick:
   detects hash mismatch → deletes prior chunk → writes new chunk →
   re-embed picks it up. Search for old text no longer returns the
   chunk; search for new text does.
6. **Failure isolation.** Per-entity chunk-write failures are logged at
   WARN with the entity name + cause; the rest of the tick continues.
   The projector returns `failed: N` in its result so worker telemetry
   can surface a degraded-state signal.
7. **Tier composition.** With `cross_layer_dedup_enabled` (Issue #455)
   ON: an entity-summary chunk and a vault chunk describing the same
   entity get deduped to the higher-scored side. With source-tier
   boost (Issue #432) ON: vault canonical content outranks the
   reference-tier entity summary on tie.
8. **CLI/MCP presentation.** Hits with `source_uri.startswith("entity://")`
   render with a `[Wikidata]` badge in CLI output and an
   `entity_summary: true` flag in the MCP envelope. No new fields on
   the underlying chunk/result types.

## E2E scenarios (`tests/e2e/test_composed_entity_summary_path.py`)

```python
@pytest.mark.e2e
def test_composed_entity_summary_path_off_is_noop():
    """Flag OFF + enrich_entity run → search for the description returns
    no entity-summary row. Pre-#457 behaviour."""

@pytest.mark.e2e
def test_composed_entity_summary_path_on_surfaces_entity_in_search():
    """End-to-end happy path:
    1. Seed Neo4j with an entity having wikidata_qid populated
    2. Run enrich_entity (hits the fake Wikidata HTTP)
    3. Flip entity_summary_indexing_enabled = True
    4. Run worker.tick() once
    5. search('AI policy research institute')
    6. Top result has source_uri starting 'entity://Q...'
    """

@pytest.mark.e2e
def test_composed_entity_summary_path_update_propagates():
    """Operator updates Wikidata description → next tick replaces the chunk."""

@pytest.mark.e2e
def test_composed_entity_summary_path_tier_composition_with_438():
    """Vault canonical chunk outranks entity-summary chunk on tie under
    default source-tier mapping (canonical x3.0 vs reference x0.6)."""
```

## BDD scenarios (operator-facing, F12 + F45)

```gherkin
# tests/bdd/features/entity_summary_indexing.feature
@entity_summary @capability_457
Feature: Operator searches find content via entity descriptions
  As an operator running kairix with enriched entities
  I want search to surface entities by their Wikidata descriptions
  So that "AI ethics organisations" finds entities tagged with that role

  @happy_path @on
  Scenario: Description-keyword query surfaces an enriched entity
    Given an entity 'Ada Lovelace Institute' is enriched with Wikidata description
    And the entity_summary_indexing_enabled flag is true
    And the worker has run a tick
    When the operator searches for 'AI policy research institute'
    Then the top results include 'Ada Lovelace Institute'
    And the result is tagged as an entity summary

  Scenario: Updating a Wikidata description refreshes the indexed chunk
    Given an entity 'Acme Corp' is enriched with description 'a software vendor'
    And the worker has run a tick
    When the operator re-enriches 'Acme Corp' with description 'an automotive parts supplier'
    And the worker runs another tick
    Then a search for 'software vendor' does not return the entity row
    And a search for 'automotive parts supplier' returns the entity row
```

## Feature flag (F51..F54)

```python
# kairix/core/features/registry.py
"entity_summary_indexing_enabled": FeatureFlag(
    name="entity_summary_indexing_enabled",
    default=False,
    description=(
        "When ON, the worker tick projects Neo4j n.summary content into "
        "the synthetic 'entity-summaries' collection so it participates "
        "in first-pass BM25 + vector retrieval. Closes #429: pre-flag, "
        "Wikidata descriptions written by enrich_entity were unreachable "
        "from search. Default OFF preserves pre-#457 behaviour. Flip ON "
        "after declaring 'entity-summaries' tier in kairix.config.yaml."
    ),
    stage="introduce",
    introduced_in=_FLAG_INTRODUCED_IN_DISPATCH_WINDOW,
    target_retire_in=_FLAG_TARGET_RETIRE_IN,
    owner="search-pipeline",
    related_spec="docs/architecture/ADR-036-entity-summary-indexing-surface.md",
),
```

Both-branch coverage (F54): OFF scenario asserts no Neo4j queries on
tick, no chunk-writer calls. ON scenario asserts at least one Neo4j
query + N chunk-writer calls equal to entity count.

## Cutover protocol (per `feature-flag-architecture.md`)

Capture-flip-soak-gate is the standard. For this flag:

1. **Pre-flip baseline.** Reflib entity-category NDCG via
   `kairix benchmark run --suite reflib`. Onboard acceptance run x3 via
   `kairix onboard check --json`. Capture per the cutover runbook.
2. **Flip flag ON** in operator config. Wait one worker tick window
   (~30s default) for the backlog to start clearing.
3. **Soak 24h** so the full backlog clears and entity chunks reach the
   `vec_index`. Monitor `failed:` counter from
   `EntitySummaryProjectionResult` via worker telemetry.
4. **Post-flip baseline.** Same reflib + onboard set.
5. **Gate:** entity-category NDCG ≥ 0.55. Onboard parity ≥ 80%. Row
   counts on the key tables (chunks / vec_index / fts_chunks) within ±2%
   of pre-flip outside of the entity-summaries collection.
6. **Rollback path:** flip flag OFF. Chunks stay in `entity-summaries`
   collection but the projector stops re-projecting. If a full unwind
   is needed: `DELETE FROM chunks WHERE collection='entity-summaries'`
   (operator-driven, documented in the cutover runbook).

## Entity-first routing (#429 Phase 2b)

Indexing (above) makes entity summaries *retrievable* — but the projector
writes them into the `entity-summaries` collection at tier `reference`
(×0.6), so they are *de-prioritised*. For an ENTITY-intent query ("tell
me about X" / "who is X") that is backwards: the operator is asking about
the entity, so its summary should lead. Phase 2b adds the routing that
flips this.

**Mechanism.** A new boost strategy
`kairix.core.search.boosts.EntityFirstRoutingBoost` multiplies the
`boosted_score` of entity-summary rows (collection `entity-summaries`, or
the well-known `entity://` source-URI prefix) by
`EntityFirstRoutingConfig.factor` (default 3.0) and re-sorts — the same
"mutate then re-sort by boosted_score" shape the `rrf` boost functions
use, so the routed summary actually leads the budget stage. It is
registered **last** in `select_boosts` (after `SourceTierBoost`) so the
multiplier composes on top of the reference de-boost.

**Two gates, both required before any score is touched:**

1. **Feature flag** — `entity_first_routing_enabled` (registry block
   below), resolved once at build time so `select_boosts` only wires the
   boost when the flag is ON, and read again at query time via the boost's
   `flag_reader` DI seam so an operator can roll the cutover back instantly
   (flag OFF ⇒ no-op) without a rebuild. Default OFF ⇒ pre-#429 ranking
   byte-for-byte.
2. **Intent** — `context["intent"] == QueryIntent.ENTITY` (with the #456
   confidence gate when `intent_confidence_gated_boosts` is ON).

The flag depends on `entity_summary_indexing_enabled` being ON for there
to be summaries to route — routing without indexing is a harmless no-op
(no `entity://` rows exist). It is orthogonal to the CLI `[Wikidata]`
badge + MCP `entity_summary` envelope flag (ADR-036 §Q7), which mark
entity rows regardless of ranking.

**Registry.**

```python
# kairix/core/features/registry.py
"entity_first_routing_enabled": FeatureFlag(
    name="entity_first_routing_enabled",
    default=False,
    stage="introduce",
    introduced_in="v2026.6.19",
    target_retire_in="v2026.12.1",
    owner="search-pipeline",
    related_spec="docs/architecture/ADR-036-entity-summary-indexing-surface.md",
)
```

**Measurement (#429 Phase 2c — runs against the now-fixed eval loop).**
Phase 1 (#552/#554) repaired `kairix eval hybrid-sweep` so per-config
scores are distinct again, which makes this measurable:

1. On a corpus that has entity summaries indexed
   (`entity_summary_indexing_enabled` ON), capture entity-category
   NDCG@10 on the curated entity slice with `entity_first_routing_enabled`
   **OFF** (baseline).
2. Flip routing **ON**, re-capture.
3. Gate on a measurable entity-category lift with no regression in other
   categories (±2pp per the cutover protocol above).

The auto-gold generator under-samples entity hard cases, so the curated
entity slice + the run live with the corpus (the reflib / production
index), not in the public repo — a meaningful slice needs real indexed
entity summaries, and corpus entity names stay out of public artefacts
(F32). The production flag-flip is **#463 / Linear PLA-173**, blocked on a
deployment apply-script fix in the infrastructure repo; the routing code
ships independently of that cutover.

## Fitness-function impact

| Rule | Concern | Resolution |
|---|---|---|
| F43 | Plugin contract test | `tests/contracts/test_entity_summary_projector_protocol.py` exercises both real impl + a `FakeEntitySummaryProjector` in `tests/fakes.py` |
| F44 | Engagement-scope code may not import firm storage | Projector runs at firm scope (Neo4j is firm-shared); chunk writer is firm-scope. No engagement-scope-side reads. Clean. |
| F45 | New capability needs BDD + outcome test | `entity_summary_indexing.feature` + `tests/integration/test_entity_summary_projector_lifecycle.py` |
| F47 | Integration tests build via factory | `lifecycle.py` constructs via `build_search_pipeline(..., paths=FakePaths(...))` and a real `EntitySummaryProjector` wired with `FakeNeo4jClient + FakePaths-backed SQLite` |
| F48 | E2E composed path | `test_composed_entity_summary_path.py` runs under Stage 4.5 with `@pytest.mark.e2e` |
| F51 | Flag has `target_retire_in` ≤ +6 months | Set per the registry block above |
| F52 | Flag call site references `REGISTRY` name | Worker tick reads `flag("entity_summary_indexing_enabled")` |
| F53 | `kairix features status` shows the flag | Automatic via registry registration |
| F54 | Both branches tested | OFF + ON BDD + integration coverage scaffolded above |
| F55 | Chunker version declared | `chunker_version="entity-summary:v1"` on every Chunk |
| F61 | `_SqliteChunkWriter` only under `kairix/core/connectors/` | Projector goes through `CollectionRouter.writer_for("entity-summaries")` — F61 clean |
| F63 | `.fetchall()` has LIMIT | Neo4j Cypher LIMIT $per_tick_max_items |
| F66 | Tick component declares per_tick_max_items + disk_watermark | Both declared on stage config |
| F68 | Each Protocol method has failure-injection contract | Five F68 tests listed above |
| F69 | Scale-bound test ≥10K | `test_projector_clears_10k_entity_backlog` |
| F77 | `sqlite3.connect` allow-listed | Projector doesn't open SQLite directly — uses ChunkWriter |

## Implementation slices

Order matters: each slice must ship green on its own with the matching
test discipline.

### Slice A — Foundation (1 PR)
- ADR-036 (this doc) lands first to lock the contract
- `EntitySummaryProjector` Protocol added to `kairix/core/protocols.py`
- `EntitySummaryProjectionResult` dataclass
- **`ChunkWriter` Protocol extension**: add
  `delete_by_source_uri(source_uri: str) -> int` returning the row count
  deleted. Update `_SqliteChunkWriter`, `_CollectionRouterChunkWriter`,
  and the existing `FakeChunkWriter` in `tests/fakes.py` to implement
  it. Update the F43 ChunkWriter contract test to exercise the new
  method on both real + fake.
- `entity_summary_indexing_enabled` flag added to `kairix.core.features.registry`
- `FakeEntitySummaryProjector` in `tests/fakes.py`
- F43 contract tests against the fake (real impl is a no-op)
- F54 integration: both-branch flag tests with the fake
- F51..F53 satisfied by the flag registration itself
- **Synthetic `wikidata` connector-config entry** declaring
  `default_sensitivity: public` (resolves the F39 question above)

Acceptance: green safe-commit; no behaviour change in production. The
ChunkWriter Protocol extension is the only call-site change other than
the new flag and Protocol additions — every existing caller still uses
`upsert(chunks)` exclusively.

### Slice B — Projector implementation (1 PR)
- `kairix/knowledge/entities/summary_projector.py` — real implementation:
  - Cypher polling query (no APOC dependency)
  - Python-side hash compute + change-detection filter
  - Chunk-from-EntitySummary builder using `tick_started_at_iso` for
    `source_modified_at`
  - Delete-prior-on-changed-hash path via the new
    `ChunkWriter.delete_by_source_uri` method landed in Slice A
  - Neo4j mark-indexed write (`SET n.summary_indexed_at,
    n.summary_indexed_content_hash`)
- Wire into worker tick chain behind the flag
- Unit tests for the helpers (chunk-builder, hash-compute, change-detection)
- F47 lifecycle integration test (seed → tick → assert chunk + FTS5 row)
- F68 failure-injection contract tests (5 listed above)
- F66 per_tick_max_items + watermark declared on stage config

Acceptance: green safe-commit; flag-OFF byte-for-byte parity with main;
flag-ON behaves per (1)..(6) of the acceptance contract.

### Slice C — E2E + BDD (1 PR)
- `tests/e2e/test_composed_entity_summary_path.py` — F48 composed-path
  with `@pytest.mark.e2e`
- `tests/bdd/features/entity_summary_indexing.feature` + steps + binding
- `tests/bdd/features/feature_flag_entity_summary_indexing_enabled.feature`
  + steps + binding
- Sample-journey query added to the reflib eval

Acceptance: green safe-commit; Stage 4.5 of CI runs `pytest -m e2e` and
the new path appears in the eval suite.

### Slice D — Operator surface + tier composition (1 PR)
- CLI/MCP renderer badge gated on `source_uri.startswith("entity://")`
- `kairix.config.yaml` example overlay block for `entity-summaries`
  tier assignment (defaults to `reference` per Q4)
- F69 soak test (`@pytest.mark.soak` — 10K-entity backlog scale-bound)
- Cutover runbook addition under
  `docs/operations/runbooks/entity-summary-cutover.md`

Acceptance: green safe-commit; renderer change visible via
`kairix search ... --include-entity-card`-equivalent output; soak
workflow stays green.

Slice E (deferred): rerank-pipeline knobs specific to entity summaries
(e.g. don't rerank entity rows past top-3 because their description is
short). Only worth doing if Slice C eval shows the rerank pass dragging
entity rows down.

## Open decisions for later

- **Multi-language entity summaries.** Today `enrich_entity` writes
  English only. If a future release writes German / Japanese summaries
  too, the projector needs a per-language chunk per entity (`source_uri
  = entity://Q12345#de`). Decision deferred until the multilingual
  enrichment story lands.
- **Wikidata "main image"** and **aliases** projection. ADR-027's
  enrichment stage already considers these. If they land, projector
  output may expand (e.g. add `entity://Q12345#aliases` chunks).
  Out-of-scope for #457.
- **Entity summary as fact-layer input.** Currently the fact-layer
  (`#340`) operates over `FactStore` records. A future tie-in could
  treat entity summaries as anchor facts. Not on the #438 critical
  path; revisit after Slice D ships.

## Definition of done (ADR-036)

- This ADR is merged into main (one PR, doc-only — qualifies for
  `safe-commit --fast`)
- Issue #457 closed referencing this ADR
- Four implementation sub-issues filed (Slices A, B, C, D) with
  acceptance criteria pulled from this document
- Issue #429 updated to track Slice C completion as its resolution
  marker

## References

- [#429](https://github.com/three-cubes/kairix/issues/429) — parent
  issue: entity n.summary written but not indexed
- [#457](https://github.com/three-cubes/kairix/issues/457) — design
  sub-issue (this ADR's home)
- [#438](https://github.com/three-cubes/kairix/issues/438) — EPIC:
  retrieval quality v2
- [#432](https://github.com/three-cubes/kairix/issues/432) — source-tier
  ranking (sets the tier model this ADR plugs into)
- [#455](https://github.com/three-cubes/kairix/issues/455) — fusion
  floor + cross-layer dedup (sets the dedup model entity-summary chunks
  participate in)
- ADR-027 — entity-enrichment worker stage (sibling design; this ADR's
  projector reuses the worker-tick + status-surface pattern)
- ADR-023 — vector-index write architecture (the embed worker that
  picks up entity-summary chunks on its existing tick)
- [`docs/architecture/feature-flag-architecture.md`](feature-flag-architecture.md)
  — flag lifecycle this ADR's flag conforms to
- [`docs/architecture/test-discipline-hardening.md`](test-discipline-hardening.md)
  — F45..F49 discipline this ADR's tests satisfy
