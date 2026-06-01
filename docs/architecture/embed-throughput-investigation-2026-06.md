# Embed throughput investigation plan — #375

**Status:** Investigation plan (2026-06-02)
**Issue:** [#375 — per-batch vec_index reload makes hourly embed loop 50× slower than expected at 1.5M vectors](https://github.com/three-cubes/kairix/issues/375)
**Related:** [#335 (closed) — vec_index OOM on 1.27M-vector mutable conversion](https://github.com/three-cubes/kairix/issues/335)

## What we know

From the 2026-06-02 production profile (1.5M-vector deployment):

- SQLite `index.sqlite`: **8.72 GB**, 2,172,983 documents
- `vectors.usearch`: **10.86 GB** on disk
- Sustained embed rate during the recovery `--force` run: **~250 chunks/sec** (with 100% cache hits)
- Expected rate for cache-only path (no real API calls): **~20,000 chunks/sec**

The 50× shortfall is the throughput gap to explain. Cache hits should be near-free — the cost is some SQLite writes + the vec_index store call. With #335's `worker_writes_vec_index` gate, the worker IS writing to the vec_index in-process.

The hypothesis: the per-batch cost of opening + appending to + saving the 10.86 GB HNSW graph dominates. usearch's `save` writes the full graph to disk each time; the `load` reconstructs the graph in RAM each time the worker restarts.

But within a single worker run, the graph stays open. So **the question is: what does each batch's vec_index touch actually cost?**

## Hypotheses (ranked by likelihood)

| H | Hypothesis | Test |
|---|---|---|
| **H1** | `vec_index.save()` runs after every batch (not at end of cycle), writing the full 10.86 GB each time | Instrument `save` call sites + count invocations per cycle; check if save is per-batch or end-of-cycle |
| **H2** | HNSW insert cost grows superlinearly past 1M vectors (graph rebalancing) | Profile `insert` latency per batch; check if it correlates with current N |
| **H3** | SQLite write contention — vec_index update + `chunks` table update + documents table update all in one transaction, with contention from MCP read queries | Profile each SQL statement's elapsed; check for `database is locked` retries |
| **H4** | The fsync at end of each batch (durability) is the cost — usearch may fsync after each save | Check `fsync` calls via `strace` on worker process |
| **H5** | Cache hit lookup itself is slow at scale — `embedding_cache.sqlite` lookup costs more as it grows | Profile `cache.get()` per chunk lookup |
| **H6** | Python overhead — per-chunk Python object construction, GIL contention with concurrent threads | Profile with cProfile during a sustained batch |

## Investigation steps

### Step 1 — Instrument the embed batch loop

Add stage timings to `kairix.core.embed.embed_pipeline.run_one_batch()` (or its current equivalent):

```python
import time

def run_one_batch(chunks: list[Chunk], cache: EmbeddingCache, vec_idx: VectorIndex, db: sqlite3.Connection) -> BatchTimings:
    t0 = time.perf_counter()
    cache_results = cache.get_many([c.hash for c in chunks])
    t_cache_lookup = time.perf_counter() - t0

    t0 = time.perf_counter()
    cache_misses = [c for c, r in zip(chunks, cache_results) if r is None]
    if cache_misses:
        vectors = embed_via_provider(cache_misses)
        cache.put_many(zip(cache_misses, vectors))
    t_provider = time.perf_counter() - t0

    t0 = time.perf_counter()
    for chunk, cached_vec in zip(chunks, cache_results):
        vec_idx.add(chunk.id, cached_vec or vectors_lookup[chunk.id])
    t_vec_insert = time.perf_counter() - t0

    t0 = time.perf_counter()
    vec_idx.save_if_due()  # whatever the current durability policy is
    t_vec_save = time.perf_counter() - t0

    t0 = time.perf_counter()
    db.executemany("UPDATE chunks SET embedded=1 WHERE id=?", [(c.id,) for c in chunks])
    db.commit()
    t_sqlite = time.perf_counter() - t0

    logger.info(
        "batch_timings batch=%d chunks=%d "
        "cache_lookup_ms=%.1f provider_ms=%.1f vec_insert_ms=%.1f vec_save_ms=%.1f sqlite_ms=%.1f total_ms=%.1f",
        ...
    )
    return BatchTimings(...)
```

Roll out under a feature flag `embed_batch_timings` (default off) so the instrumentation doesn't run in normal production. F-rule compliance: declare the flag in `kairix.core.features.registry` with `target_retire_in: v2027.6.30`.

### Step 2 — Capture a representative profile

On the live VM, enable the flag, run the embed for ~5 minutes of cache-hit-only work (the recovery scenario), grep `batch_timings` from the worker logs, and aggregate:

```bash
docker logs --since=5m app-kairix-worker-1 \
  | grep batch_timings \
  | awk '{ for(i=1;i<=NF;i++) if($i~/_ms=/){print $i} }' \
  | sort | datamash -t= --header-in mean 2 stddev 2 max 2
```

Expected shape: one of `vec_insert_ms`, `vec_save_ms`, or `sqlite_ms` dominates. That's the hot path.

### Step 3 — Branch on the hot stage

| If hot stage is… | The fix is… |
|---|---|
| `vec_save_ms` | Move to "save every N batches" or "save at end of cycle" durability policy. The save() is the 10.86 GB write. |
| `vec_insert_ms` | usearch parameter tuning (`expansion_add`, `M`); or partition the vec_index by collection so each insert touches a smaller graph |
| `sqlite_ms` | Batch the `chunks` UPDATE into one statement with VALUES; consider WAL mode if not already; reduce transaction frequency |
| `cache_lookup_ms` | Add an index on `embedding_cache.embeddings(chunk_hash)`; or switch the cache to a memory-mapped file format |
| `provider_ms` (during cache-miss cycles) | Already covered by batching + retries; not the recovery-cycle bottleneck |

### Step 4 — Pick the fix shape

Two structural options once we know the hot stage:

**Option A — Keep `worker_writes_vec_index: True`, optimise the save policy.**
- Worker writes vectors to vec_index in-process
- Save only at end of cycle (not per batch) — much cheaper at the cost of crash-window data loss
- The crash-recovery path (#127) handles the data-loss case
- Pros: minimal architectural change; one config flag flip + a save-policy refactor
- Cons: still requires the worker to hold 10.86 GB in RAM during the cycle

**Option B — Default `worker_writes_vec_index: False`, run `kairix index-rebuild` out-of-band.**
- Worker only writes to `content_vectors` in SQLite (small per-row)
- A separate scheduled process (cron / systemd timer) rebuilds the vec_index from SQLite periodically
- Pros: worker stays small; vec_index can use a larger RAM ceiling without affecting MCP
- Cons: vec_index lags SQLite by N hours; some retrieval queries hit unindexed vectors

Option A is the lower-risk near-term fix. Option B is the right long-term shape but requires more design.

### Step 5 — Ship the fix with a feature-flag cutover

Per the cutover protocol from `docs/architecture/feature-flag-architecture.md`:

1. New flag (e.g. `vec_index_save_policy_end_of_cycle` for Option A)
2. Capture baseline (embed throughput + recall + latency) before flip
3. Flip flag, soak 24h
4. Diff against baseline; require throughput ≥ 5,000 chunks/sec at 1.5M scale (acceptance from #375)
5. Promote or rollback

### Step 6 — Backstop test

Add a soak test `tests/soak/test_embed_throughput_at_scale.py`:

```python
@pytest.mark.soak
def test_embed_throughput_meets_floor_at_1m_vectors(...):
    # Seed 1M vectors via the canonical factory
    # Run a 1k-chunk embed batch with 100% cache hits
    # Assert: sustained rate >= 5,000 chunks/sec
    # Sabotage proof: revert the save-policy fix → test fails
```

Runs nightly via `soak-suite.yml`. Prevents regression.

## Decision tree

```
Run Step 1+2 (instrument + profile)
    ├── Hot stage = vec_save_ms?
    │     ├── Yes → Option A (end-of-cycle save)
    │     └── No  → continue
    ├── Hot stage = vec_insert_ms?
    │     ├── Yes → usearch tuning OR collection-partitioned index
    │     └── No  → continue
    ├── Hot stage = sqlite_ms?
    │     ├── Yes → batch UPDATE + WAL + transaction tuning
    │     └── No  → continue
    └── Hot stage = cache_lookup_ms?
          └── Yes → cache schema/index optimisation
```

## What this does NOT address

- The fact that ANALYZE was never run on production (separate issue: #376)
- The OOM scenario from #335 (closed; the gating works, this issue is about the throughput cost of the gating choice)
- Search-time vec_index read latency (a separate concern — read path doesn't have the same per-batch save overhead)

## Effort estimate

| Step | Effort |
|---|---|
| Step 1 (instrument) | 1 PR, ~150 LOC + tests, half a day |
| Step 2 (capture profile) | 30 min on production |
| Step 3 (branch decision) | 1h analysis after profile |
| Step 4 (pick + design fix) | 1-2 day design depending on Option A vs B |
| Step 5 (implement + flag) | 1-2 days |
| Step 6 (soak test) | half a day |

**Total: ~1 week of focused work.** Worth doing before any further scaling (the gap widens as the corpus grows).
