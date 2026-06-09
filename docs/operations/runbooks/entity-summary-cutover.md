# Entity-summary indexing — cutover runbook

**Scope:** Flip `entity_summary_indexing_enabled` from `false` (default)
to `true` on a kairix deployment that has already run
`kairix entity enrich` against its Neo4j store so the synthetic
`entity-summaries` collection becomes part of first-pass BM25 +
vector retrieval.

**Background:** ADR-036 locks the architecture. Issues #459 / #460 /
#461 / #462 land the implementation. This runbook is what an operator
actually runs to turn the feature on safely.

## Status — Customer Zero cutover 2026-06-09

The Customer Zero (kairix-on-kairix) deployment ran this cutover
against the v2026.6.9a1 alpha image. Operator overlay at
`/etc/kairix/kairix.config.yaml` now carries
`entity_summary_indexing_enabled: true` + the `entity-summaries`
synthetic collection declaration. Worker restarted to pick up the
config. Soak-validation in progress; issue
[kairix#429](https://github.com/three-cubes/kairix/issues/429) stays
open until the 24h soak completes and a post-flip baseline is captured.

Pre-flip headline numbers (from `/var/lib/kairix/cutover/<DATE>-pre-flip/`):

| Metric | Value | Gate |
|---|---|---|
| entity NDCG@10 | 0.800 (n=15) | ≥ 0.55 ✅ |
| temporal NDCG@10 | 0.558 (n=20) | — |
| multi_hop NDCG@10 | 0.724 (n=15) | — |
| weighted total | 0.808 | — |
| onboard | 18/18 | fully_passed |

The ADR-036 §Cutover gate (entity-NDCG ≥ 0.55) was already
structurally satisfied pre-flip, so the cutover validates the
projector mechanics in production rather than chasing a measurable
quality lift. Post-flip baseline + diff lands at
`/var/lib/kairix/cutover/2026-06-09-post-flip/` after the soak.

## When to run this

- Your kairix deployment has populated `n.summary` on entity nodes
  (typically via `kairix entity enrich`)
- The reflib entity-category NDCG measurement is the eval gap you're
  targeting (target ≥ 0.55 per ADR-036 §Cutover)
- You're not in a release-freeze window

## Where things live on the VM

The cutover steps assume a docker-compose deploy (the canonical
production layout). Adapt paths if your topology differs.

| Where | Path | What's there |
|---|---|---|
| Host config | `/etc/kairix/kairix.config.yaml` | Operator overlay (bind-mounted into the container at the same path) |
| Container | `app-kairix-1` | Runs the kairix CLI (`docker exec app-kairix-1 kairix ...`) |
| Cutover artefacts | `/var/lib/kairix/cutover/<YYYY-MM-DD>-{pre,post}-flip/` | Benchmark JSON + feature-status snapshots. Inside the container as `/var/lib/kairix/cutover/...`; bind-mounted to the same host path. |
| DB | `/var/lib/kairix/index.sqlite` (container path = host path) | Chunks, embeddings, FTS index |

## Pre-flip — capture the baseline

The post-flip soak compares against these numbers. Capture them
**before** flipping the flag so the comparison is meaningful.

```bash
DATE=$(date +%Y-%m-%d)
mkdir -p /var/lib/kairix/cutover/$DATE-pre-flip

# 1. Reflib eval baseline — 242-case suite (entity / temporal / multi-hop / etc.)
docker exec app-kairix-1 kairix benchmark run \
  --suite reflib \
  --output /var/lib/kairix/cutover/$DATE-pre-flip/

# 2. Feature-flag snapshot — confirms entity_summary_indexing_enabled is currently OFF
docker exec app-kairix-1 kairix features status \
  > /var/lib/kairix/cutover/$DATE-pre-flip/features.txt

# 3. Onboard acceptance run x3 (replaces "sample-journey")
for i in 1 2 3; do
  docker exec app-kairix-1 kairix onboard check --json \
    > /var/lib/kairix/cutover/$DATE-pre-flip/onboard-$i.json
done

# 4. State digest — row counts on the key tables
docker exec app-kairix-1 sh -c '
  for t in chunks vec_index fts_chunks mcp_call_log; do
    n=$(sqlite3 /var/lib/kairix/index.sqlite "SELECT COUNT(*) FROM $t" 2>/dev/null || echo "n/a")
    echo "$t $n"
  done
' > /var/lib/kairix/cutover/$DATE-pre-flip/state.txt
```

The post-flip step compares against this directory.

> **Why `kairix benchmark run` not `kairix eval`** — the legacy `kairix
> eval <suite_path>` surface is being removed in favour of the unified
> quality CLI. Use `kairix benchmark run --suite <name>` here so the
> runbook still works after the deprecation lands.

## Flip the flag

Add the entity-summary tier mapping AND flip the flag in your
operator-side `/etc/kairix/kairix.config.yaml`:

```yaml
# /etc/kairix/kairix.config.yaml — operator overlay
collections:
  shared:
    - name: entity-summaries
      tier: reference   # default per ADR-036 §Q4; flip to `canonical`
                        # only if you want Wikidata to outrank vault
                        # content on tie, which is rare

feature_flags:
  entity_summary_indexing_enabled: true
```

Restart the kairix service so the new config takes effect:

```bash
cd /opt/kairix/app && docker compose restart kairix
```

The worker tick will start projecting entities into the
`entity-summaries` collection on its next cadence. Verify the flag
read ON:

```bash
docker exec app-kairix-1 kairix features status | grep entity_summary
# expect: entity_summary_indexing_enabled: true (set via config)
```

## Soak

Wait **at least 24 hours**. The worker projects up to 200 entities
per tick (`per_tick_max_items` per F66); a 7,461-entity backlog
clears in ~38 ticks (~19 min on the default 30-second tick), but
the embed worker needs additional time to vectorise the new chunks
into `vec_index`.

While the soak runs, monitor:

- **Projection telemetry**
  ```bash
  docker exec app-kairix-1 kairix features status | grep entity_summary
  # confirm the flag still reads ON
  ```
  Projection counters are written to the worker log:
  ```bash
  docker exec app-kairix-1 sh -c 'tail -n 200 /var/log/kairix/worker.log' \
    | grep -A1 EntitySummaryProjector
  # look for `projected`, `updated`, `skipped`, `failed` from the
  # most recent EntitySummaryProjectionResult
  ```
  And query the synthetic collection's chunk count directly:
  ```bash
  docker exec app-kairix-1 sqlite3 /var/lib/kairix/index.sqlite \
    "SELECT collection, COUNT(*) FROM chunks GROUP BY collection ORDER BY 2 DESC LIMIT 10;"
  ```

- **Failure counter**
  If `failed` is non-zero over multiple ticks, check the worker log
  for `EntitySummaryProjector: per-entity tick failed` lines and
  triage the underlying Neo4j or chunk-write error. Per-entity
  failures don't abort the tick (failure isolation per ADR-036
  §Expected behaviours #6).

## Post-flip — capture the same baseline + compare

After the 24h soak:

```bash
DATE=$(date +%Y-%m-%d)
mkdir -p /var/lib/kairix/cutover/$DATE-post-flip

docker exec app-kairix-1 kairix benchmark run \
  --suite reflib \
  --output /var/lib/kairix/cutover/$DATE-post-flip/

docker exec app-kairix-1 kairix features status \
  > /var/lib/kairix/cutover/$DATE-post-flip/features.txt

for i in 1 2 3; do
  docker exec app-kairix-1 kairix onboard check --json \
    > /var/lib/kairix/cutover/$DATE-post-flip/onboard-$i.json
done

docker exec app-kairix-1 sh -c '
  for t in chunks vec_index fts_chunks mcp_call_log; do
    n=$(sqlite3 /var/lib/kairix/index.sqlite "SELECT COUNT(*) FROM $t" 2>/dev/null || echo "n/a")
    echo "$t $n"
  done
' > /var/lib/kairix/cutover/$DATE-post-flip/state.txt
```

Diff the four pairs:

```bash
PRE=/var/lib/kairix/cutover/$(ls /var/lib/kairix/cutover | grep pre-flip | tail -1)
POST=/var/lib/kairix/cutover/$(ls /var/lib/kairix/cutover | grep post-flip | tail -1)

# Reflib eval (entity-category NDCG is the headline number)
docker exec app-kairix-1 kairix benchmark compare \
  --baseline "$PRE"/reflib-result.json \
  --candidate "$POST"/reflib-result.json

# State digest
diff "$PRE"/state.txt "$POST"/state.txt

# Feature flag
diff "$PRE"/features.txt "$POST"/features.txt
```

The exact JSON filename inside the `--output` directory depends on
the suite + run (kairix benchmark run writes `<suite>-result.json`).
Use `ls $PRE` to confirm before passing to `benchmark compare`.

## Gate

The cutover **passes** when:

- Entity-category NDCG ≥ **0.55** (per ADR-036 §Cutover)
- Onboard parity ≥ **80%** between pre/post (no widespread
  regressions on non-entity queries)
- State-digest delta within ±2% (no unintended writes elsewhere)
- `failed` counter from the projection telemetry trended to 0 by
  the end of the soak (an occasional transient failure is fine; a
  steady non-zero level is not)

If any of those fails, **rollback** below.

## Rollback

If the gate fails or production behaviour regresses unexpectedly:

1. **Flip the flag OFF in `/etc/kairix/kairix.config.yaml`**
   ```yaml
   feature_flags:
     entity_summary_indexing_enabled: false
   ```
   Restart kairix:
   ```bash
   cd /opt/kairix/app && docker compose restart kairix
   ```
   The worker projector stops on the next tick. Already-projected
   chunks remain in the `entity-summaries` collection (idempotent —
   they don't keep growing because the projector isn't running).

2. **Full unwind (rare — only if the chunks themselves are wrong)**
   ```bash
   docker exec app-kairix-1 sqlite3 /var/lib/kairix/index.sqlite \
     "DELETE FROM chunks WHERE collection = 'entity-summaries';"
   ```
   The next tick (when re-enabled) re-projects from Neo4j.

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
