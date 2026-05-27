# ADR-023 — Vector index write architecture (post-#335 interim → permanent)

**Status:** Proposed 2026-05-28 — **DECISION NEEDED** before further code lands on #335
**Issues:** #335 (embed worker OOM on full vec_index rebuild)
**Related:** ADR-019 (compose-layer resource governance — the 1 GiB worker ceiling that surfaces this), ADR-020 (per-tick budget — independent), ADR-021 (per-source metadata — independent)

## Context

`086a604d` shipped the interim fix for #335 — the env-gated worker write (`KAIRIX_WORKER_WRITES_VEC_INDEX`, default OFF). With the gate on, the worker stops OOMing; SQLite `content_vectors` (metadata table) continues to advance; the on-disk `vectors.usearch` file goes **stale** against new embeddings until rebuilt out-of-band.

The interim leaves two architectural questions open:

1. **Where do the actual embedding floats live?** Today they live exclusively in `vectors.usearch`. `content_vectors` schema is `(hash, seq, pos, model, embedded_at, chunk_date)` — metadata only. So "rebuild from SQLite" cannot work without storing the vectors somewhere reproducible.
2. **How does the worker push new vectors into the on-disk index without loading the whole index into RAM first?** HNSW fundamentally requires the existing graph resident to add nodes (edge connectivity), so naïve in-process append against a 1M+ vector base hits the same 7.8 GB ceiling that OOM-killed the worker.

The two failure modes compose: even if (2) were solved by giving the worker more RAM, future rebuilds would still hit (1) because the SQLite source of truth doesn't contain the vectors.

This ADR forces the decision between two architecturally distinct paths, both of which solve (1) and (2) but with different operational shapes.

## Decision required

Pick one of A1 / A2 / Hybrid below, then strike "DECISION NEEDED" from the status line.

### Option A1 — vector bytes as a BLOB column on `content_vectors`

Add `vector_bytes BLOB NOT NULL` to `content_vectors`. New embeddings write the float-32 bytes directly into SQLite alongside metadata. Existing 1.27M vectors get one-off backfilled from the current `vectors.usearch` file (the backfill needs the same ~7.8 GB ceiling once, run in a sidecar container with appropriate RAM). After backfill: SQLite is single source of truth.

`kairix index-rebuild` CLI streams `vector_bytes` from SQLite in batches, builds a fresh usearch on disk, atomic-renames. RAM bound is `batch_size × ndim × 4 B` plus the in-construction HNSW graph — for a 1M-vector index that's still ~7.8 GB resident, but the rebuild runs in a dedicated process whose ceiling can be set independently of the worker.

**Pros:**

- Single source of truth. SQLite + filesystem backups already cover the vectors; usearch becomes derived state.
- Future rebuilds work without touching the old usearch file at all. Disaster recovery: drop usearch, run rebuild, restored.
- Read path unchanged — MCP container still does `Index.restore(view=True)` on the rebuilt file.
- No multi-segment merging logic. Mental model stays simple.
- Plays cleanly with the schema-writer-symmetry F-rule that #336 would add (`pushed_to_X` style).

**Cons:**

- **Storage cost.** 1.27M vectors × 1536 dims × 4 B = ~7.8 GB added to `index.sqlite`. The DB is currently 9.4 GB on disk; after backfill it would be ~17 GB. Disk is at 40% (24 GB / 63 GB) so headroom exists, but it's a non-trivial doubling.
- **One-off backfill needs the same RAM as the current OOM.** Operator runs in a sidecar with 10 GiB ceiling, runs once, done. But it's a real bootstrap step.
- **Rebuild is still O(corpus) in RAM** even though the source of truth changed. The OOM moves from "every embed cycle" to "every rebuild" — solved by running rebuild in a dedicated process with higher mem ceiling, but the underlying constraint hasn't gone away.
- **Vector staleness window during rebuild.** New embeddings written to SQLite while a rebuild is in flight don't appear in the index until the next rebuild. Acceptable if rebuild cadence < embed cadence × N.

### Option A2 — delta segments

Worker writes new vectors to a small mutable `vectors.delta-NNNNNN.usearch` file (one per worker tick or per N batches). Base `vectors.usearch` stays untouched. MCP search opens base + all live deltas, fans out the search across each, merges results by cosine score, returns top-K.

Background **compaction job** periodically (nightly / on segment count > N) folds the deltas into a fresh base via the same rebuild logic A1 needs, then atomically swaps base + deletes consumed deltas.

**Pros:**

- **No schema change.** `content_vectors` stays metadata-only; existing queries / migrations unaffected.
- **No SQLite bloat.** Vectors stay in usearch's compact native format (HNSW + quantized floats).
- **Worker memory bound is tiny.** Each delta is small (per-tick batch worth of vectors = ~250 × 1536 × 4 B = 1.5 MB resident). No giant rebuild on the hot path.
- **No backfill.** Existing `vectors.usearch` becomes the base, untouched. Delta starts empty.
- **Industry pattern.** Lucene segments, Pinecone shards, Solr — all variants of this. Well-understood operationally.

**Cons:**

- **Read-path complexity.** MCP must enumerate live deltas at search time (cheap with mtime poll) and merge results. The merge logic is straightforward (k-way union, sort by score, truncate top-K) but it's new code on the latency-critical path.
- **Compaction job needed.** Without it, segment count grows unbounded and read latency degrades linearly. So we end up writing the same rebuild code A1 needs anyway — it's just gated on segment count, not run on every embed cycle.
- **MCP cache invalidation.** Currently MCP loads usearch once at startup via mmap. With deltas, MCP needs to detect new delta files and load them (mtime poll? signal? file-watcher?). Restart-on-detect is simplest but kills the hot cache; signal-based reload is fiddlier.
- **Vector floats only in usearch.** If a delta file gets corrupted, those embeddings are lost (re-embedding regenerates them, but it's a cost). A1 has the SQLite copy as a safety net.
- **Atomic swap complexity at compaction.** Need to handle MCP holding old base mmap while we write new base; rename + reload sequence isn't trivial.

### Hybrid — A1 schema + A2 delta writes (during transition)

Add `vector_bytes BLOB` to `content_vectors` (A1's schema change) AND have the worker write deltas during the day (A2's append shape). Nightly compaction job reads `vector_bytes` from SQLite for any signal not already in the base, builds fresh base, swaps. Delta files become a transient cache between embed and compaction.

**Pros:** Single source of truth (A1's win) + small worker memory bound (A2's win). Compaction job is simpler than A2 because the rebuild source is SQLite (not delta merge).

**Cons:** Largest diff. More moving parts. Storage cost = A1's storage cost (~+7.8 GB SQLite).

## Tradeoff comparison

| Dimension | A1 | A2 | Hybrid |
|---|---|---|---|
| Schema change | Yes (BLOB col) | No | Yes |
| Storage cost | +7.8 GB | minimal | +7.8 GB |
| Worker RAM in hot loop | n/a (rebuild offline) | ~2 MB | ~2 MB |
| Rebuild RAM | ~10 GB (sidecar) | ~10 GB (sidecar) | ~10 GB (sidecar) |
| Read path code change | None | Significant | Some (delta-aware reads until compaction) |
| Compaction needed | No (rebuild on demand) | Yes (scheduled) | Yes (scheduled) |
| Backfill needed | Yes (one-off, sidecar) | No | Yes (one-off) |
| Disaster recovery | Drop usearch, rebuild | Re-embed from chunks | Drop usearch, rebuild |
| Industry pattern | Pinecone / Weaviate | Lucene / Solr / ES | (custom) |
| Complexity score | Low | Medium-high | Medium |
| Time to ship | ~1 day | ~3-5 days | ~3-4 days |

## Recommendation

**A1.** Three reasons:

1. **Operational simplicity dominates** at kairix's scale (1-10M vectors). Lucene-style segments earn their complexity at 100M+ vectors with high write rate. We're not there and won't be soon.
2. **Storage cost is real but acceptable.** 7.8 GB on a 63 GB disk is a 12 % headroom hit; we'd see it coming long before it bites.
3. **Disaster recovery is qualitatively better.** SQLite as the source of truth means filesystem snapshots / rsync backups cover the vectors. With A2 the vectors are only in usearch and corruption means re-embedding (Azure cost + time).

The Hybrid is appealing on paper but adds the segment / compaction complexity from A2 on top of A1's schema change — strictly more moving parts than either pure option.

The 1-day estimate for A1 includes: schema migration + one-off backfill CLI + `kairix index-rebuild` CLI + worker tick that fires rebuild subprocess when drift exceeds N + BDD + integration + retire the `KAIRIX_WORKER_WRITES_VEC_INDEX` interim gate.

## Open questions for the decider

1. Pick A1 / A2 / Hybrid.
2. Rebuild cadence — operator-triggered only (manual) or worker-scheduled when drift > N? If scheduled, where does the subprocess run (kairix-1 container with 3 GiB? Dedicated sidecar?).
3. Drift threshold for auto-rebuild — 10k vectors? 24h since last rebuild? Both?
4. Should the rebuild block embedding while running (writer lock) or run concurrently (rely on atomic rename)?

## Acceptance criteria (filled in once A1 / A2 / Hybrid chosen)

To be expanded once the path is picked.

## Migration

- **Phase 1 (shipped, `086a604d`):** Interim env gate, default OFF. Worker no longer OOMs.
- **Phase 2 (this ADR):** Pick A1 / A2 / Hybrid. Implement.
- **Phase 3:** Retire `KAIRIX_WORKER_WRITES_VEC_INDEX` env gate once the architectural fix proves out for 1+ release. Gate becomes vestigial.
