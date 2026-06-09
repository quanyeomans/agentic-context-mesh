# Entity-summary indexing — cutover runbook

**Scope:** Flip `entity_summary_indexing_enabled` from `false` (default)
to `true` on a kairix deployment that has already run
`enrich_entity` against its Neo4j store so the synthetic
`entity-summaries` collection becomes part of first-pass BM25 +
vector retrieval.

**Background:** ADR-036 locks the architecture. Issues #459 / #460 /
#461 land the implementation. This runbook is what an operator
actually runs to turn the feature on safely.

## When to run this

- Your kairix deployment has populated `n.summary` on entity nodes
  (typically via `kairix entity enrich`)
- The reflib entity-category NDCG measurement is the eval gap you're
  targeting (current 0.380 → target ≥ 0.55 per ADR-036 §Cutover)
- You're not in a release-freeze window

## Pre-flip — capture the baseline

The post-flip soak compares against these numbers. Capture them
**before** flipping the flag so the comparison is meaningful.

1. **Reflib eval baseline**
   ```bash
   kairix benchmark run --mode eval --suite reflib > /tmp/eval-pre-flip.json
   ```
   Note the entity-category NDCG line. Expected ~0.380.

2. **Sample-journey x3**
   ```bash
   for i in 1 2 3; do
     kairix benchmark run --mode sample-journey > /tmp/journey-pre-$i.json
   done
   ```

3. **State digest**
   ```bash
   kairix doctor --state-digest > /tmp/state-pre-flip.txt
   ```

Save all four files. The post-flip step compares against them.

## Flip the flag

Add the entity-summary tier mapping AND flip the flag in your
operator-side `kairix.config.yaml` overlay:

```yaml
# kairix.config.yaml — operator overlay
collections:
  shared:
    - name: entity-summaries
      tier: reference   # default per ADR-036 §Q4; flip to `canonical`
                        # only if you want Wikidata to outrank vault
                        # content on tie, which is rare

feature_flags:
  entity_summary_indexing_enabled: true
```

Restart kairix or `kairix doctor --reload-config` to pick up the
change. The worker tick will start projecting entities into the
`entity-summaries` collection on its next cadence.

## Soak

Wait **at least 24 hours**. The worker projects up to 200 entities
per tick (`per_tick_max_items` per F66); a 7,461-entity backlog
clears in ~38 ticks (~19 min on the default 30-second tick), but
the embed worker needs additional time to vectorise the new chunks
into `vec_index`.

While the soak runs, monitor:

- **Projection telemetry**
  ```bash
  kairix features status | grep entity_summary
  # confirm the flag reads ON
  ```
  And the projection counters surface via `kairix doctor`:
  ```bash
  kairix doctor 2>&1 | grep -A1 entity_summary
  # look for `projected`, `updated`, `skipped`, `failed` from the
  # most recent EntitySummaryProjectionResult
  ```

- **Failure counter**
  If `failed` is non-zero over multiple ticks, check the worker log
  for `EntitySummaryProjector: per-entity tick failed` lines and
  triage the underlying Neo4j or chunk-write error. Per-entity
  failures don't abort the tick (failure isolation per ADR-036
  §Expected behaviours #6).

## Post-flip — capture the same baseline + compare

After the 24h soak:

1. **Reflib eval re-run**
   ```bash
   kairix benchmark run --mode eval --suite reflib > /tmp/eval-post-flip.json
   ```

2. **Sample-journey x3**
   ```bash
   for i in 1 2 3; do
     kairix benchmark run --mode sample-journey > /tmp/journey-post-$i.json
   done
   ```

3. **State digest**
   ```bash
   kairix doctor --state-digest > /tmp/state-post-flip.txt
   ```

4. **Diff the four pairs**
   ```bash
   diff /tmp/eval-pre-flip.json /tmp/eval-post-flip.json
   diff /tmp/state-pre-flip.txt /tmp/state-post-flip.txt
   ```
   And compare the sample-journey runs visually.

## Gate

The cutover **passes** when:

- ✅ Entity-category NDCG ≥ **0.55** (target +0.17 absolute over 0.380)
- ✅ Sample-journey parity ≥ **80%** between pre/post (no widespread
  regressions on non-entity queries)
- ✅ State-digest delta within ±2% (no unintended writes elsewhere)
- ✅ `failed` counter from the projection telemetry trended to 0 by
  the end of the soak (an occasional transient failure is fine; a
  steady non-zero level is not)

If any of those fails, **rollback** below.

## Rollback

If the gate fails or production behaviour regresses unexpectedly:

1. **Flip the flag OFF**
   ```yaml
   # kairix.config.yaml — operator overlay
   feature_flags:
     entity_summary_indexing_enabled: false
   ```
   Reload config. The worker projector stage stops on the next tick.
   Already-projected chunks remain in the `entity-summaries`
   collection (idempotent — they don't keep growing because the
   projector isn't running).

2. **Full unwind (rare — only if the chunks themselves are wrong)**
   ```sql
   DELETE FROM chunks WHERE collection = 'entity-summaries';
   ```
   Run inside `sqlite3 /var/lib/kairix/kairix.db`. The next tick
   (when re-enabled) re-projects from Neo4j.

3. **File a follow-up issue** with the diff output + the entity-NDCG
   measurement so the gap can be triaged before the next cutover
   attempt.

## What an operator sees after the cutover

- **CLI** — `kairix search <query>` results that came from the
  entity-summaries collection now render a `[Wikidata]` suffix on
  the title line so the operator can tell them apart from vault
  content at a glance.
- **MCP envelope** — agent search responses include
  `entity_summary: true` on the per-hit dict for the same rows. An
  agent that wants to treat Wikidata-sourced context differently
  (e.g. cite differently, render a small badge) gates on this flag.
- **`kairix features status`** — surfaces the flag's current state
  (the F53 contract) so it's discoverable without grepping config.

## References

- ADR-036 — `docs/architecture/ADR-036-entity-summary-indexing-surface.md`
- ADR-036 §Cutover — the canonical specification this runbook
  operationalises
- #429 — the parent issue whose eval-NDCG gap this runbook closes
- #432 — source-tier ranking (sets the tier-mapping vocabulary this
  runbook uses for `tier: reference`)
- [Feature-flag architecture](../../architecture/feature-flag-architecture.md)
  — the canonical default-safe + both-branch-tested cutover model
