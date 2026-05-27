# ADR-023 — Vector index write architecture (post-#335)

**Status:** Accepted 2026-05-28 — operational fix first; architectural fallbacks specified for trigger conditions
**Issues:** #335 (embed worker OOM on full vec_index rebuild)
**Related:** ADR-019 (compose resource governance — the 1 GiB cgroup ceiling that surfaces #335), ADR-020 / ADR-021 (Wave E.5 — independent), [`docs/operations/runbooks/worker-memory-and-swap.md`](../operations/runbooks/worker-memory-and-swap.md)
**Supersedes:** the initial proposed-state of this ADR (A1 / A2 / Hybrid framing) — superseded after researching the original usearch decision and modelling the scaling curve.

## Context

`086a604d` shipped an interim env-gate (`KAIRIX_WORKER_WRITES_VEC_INDEX`, default OFF) that stops the worker's OOM loop by skipping the in-process usearch open + write entirely. SQLite `content_vectors` (metadata only) continues to advance; the on-disk `vectors.usearch` goes stale against new embeddings until the gate is re-enabled.

The ADR's job is to specify what happens after the gate — does the architecture need to change, or is this an operational tuning problem?

## Decision history

`vec_index.py` and the usearch backend landed in v2026.5.1 (commit `25705d17`). The choice replaced an earlier sqlite-vec backend (KFEAT-009 in v2026.4.24a3) which itself replaced QMD (Node.js) vector search. The path was QMD → sqlite-vec → usearch over ~2 months.

The reasons for the sqlite-vec → usearch swap, from contemporary issues + docs:

1. **Extension-loading ceremony.** sqlite-vec required `db.enable_load_extension(True); db.load_extension(<path>)` at every connection, plus runtime path discovery for `vec0.so`. [#30](https://github.com/three-cubes/kairix/issues/30) documents operators finding the .so at paths like `/data/tools/qmd/node_modules/.pnpm/sqlite-vec-linux-x64@0.1.7-alpha.2/...`. [#83](https://github.com/three-cubes/kairix/issues/83) cleaned up the remaining sqlite-vec references after the swap. usearch is `import usearch; Index(...)` — zero ceremony.
2. **HNSW maturity at the time.** sqlite-vec's HNSW was nascent in early 2026; usearch's was mature.
3. **Self-contained "just pip install".** The whole v2026.4.24a3 → v2026.5.1 arc was about killing operational friction in the deploy story.

`docs/operations/OPERATIONS.md:170` enshrined this: *"usearch: Installed automatically as a pip dependency (`usearch>=2.0`). No manual extension path configuration needed."*

The original decision was sound. The OOM in #335 is a **scale regression** of the per-vector RAM cost catching up with the cgroup ceiling, not a design flaw.

## Scaling model

Per-vector RAM in a usearch HNSW index (default `M=16`, f32 dtype):

- Vector data: `ndim × 4 bytes` = 1536 × 4 = 6.1 KB
- HNSW graph overhead: ~256 bytes per node (edge lists + level data)
- **~6.4 KB per vector total resident**

Linear in corpus size with a known constant:

| Corpus | RAM resident | Comment |
|---|---|---|
| 100k | 640 MB | fits inside default 1 GiB cgroup |
| 500k | 3.2 GB | fits inside main kairix container (3 GiB) but not worker |
| **1.27M (today)** | **8 GB** | hits the OOM ceiling this ADR addresses |
| 5M | 32 GB | needs swap or bigger host |
| 10M | 64 GB | needs significantly more swap or bigger host |
| 50M | 320 GB | beyond commodity VM |

## Read vs write asymmetry

usearch supports two open modes:

- `Index.restore(path, view=True)` — read-only mmap, zero RAM allocation. MCP container uses this.
- `Index.restore(path, view=False)` — mutable in-RAM. Worker container uses this (via `_ensure_mutable` rebuild) to add new vectors.

The asymmetry is fundamental to HNSW: graph mutations need the existing graph resident to compute edge connectivity. There is no "mmap mutable" mode in usearch (or any HNSW library — the graph topology lookup pattern doesn't tolerate per-edge page faults at write rate).

So the constraint is **on the writer only**. Reads scale gracefully via kernel page cache regardless of corpus size.

## Decision — primary path: operational tuning with host swap

**Re-enable the worker write path; let the cgroup spill into host swap during the embed cycle's mutable-add step.**

Mechanism:

- Compose hook for `KAIRIX_WORKER_MEMSWAP_LIMIT` (this PR — `docker-compose.yml`). Defaults to `1g` (Docker default → zero swap allowed) so existing deployments are unaffected.
- Operator runbook at [`docs/operations/runbooks/worker-memory-and-swap.md`](../operations/runbooks/worker-memory-and-swap.md) walks through: verify host swap → raise `KAIRIX_WORKER_MEM_LIMIT` to 8g → raise `KAIRIX_WORKER_MEMSWAP_LIMIT` to 16g → set `KAIRIX_WORKER_WRITES_VEC_INDEX=1` → recreate worker container.
- HNSW's locality property (~200 graph node visits per insert) keeps page-fault rate bounded; embed cycles take 2-10 minutes longer per cycle under swap pressure, which is acceptable for a background job.

**Why this is sufficient at our scale:**

| Constraint | Status |
|---|---|
| Read latency unaffected by index size beyond working set | ✓ — mmap-backed |
| Write latency degraded but bounded | ✓ — minutes per cycle, not hours |
| Disk cost for swap | trivial — ~$5/month for 32 GB SSD swap on Azure |
| Code change required | none |
| Schema migration | none |
| Backend swap | none |
| Operational complexity added | small — one env knob, documented runbook |

## Architectural fallbacks (trigger conditions, not now)

The interim gate stays in place as a kill-switch. If any of the trigger conditions below fire, adopt the matching architectural change. None are speculative — each ties to a measurable failure mode.

### A1 — vector bytes as a BLOB column on `content_vectors`

**Triggers:**

- `vectors.usearch` file corrupts in production AND re-embedding the full corpus is operationally unacceptable (Azure cost or time)
- Corpus exceeds 10M vectors AND the rebuild-from-scratch path needs to run on a host that doesn't have the original file

**Shape:** Add `vector_bytes BLOB NOT NULL` to `content_vectors`. New embeddings write the f32 bytes directly into SQLite alongside metadata. One-off backfill from existing usearch (needs ~10 GB sidecar). After backfill: SQLite is single source of truth; `kairix index-rebuild` streams from SQLite, builds a fresh usearch on disk, atomic-renames.

**Cost when adopted:** ~+7.8 GB to `index.sqlite` (doubles vector storage). Disaster-recovery from SQLite file becomes trivial.

### A2 — delta segments

**Triggers:**

- Worker embed cycle wall-clock consistently exceeds the cycle schedule (default 3600s), even with adequate swap
- Operator wants new embeddings searchable within seconds, not hours (current freshness window is "next embed cycle")
- Corpus exceeds 20M vectors AND swap-based path saturates disk IO

**Shape:** Worker writes new vectors to a small mutable `vectors.delta-NNNNNN.usearch` file (one per cycle). Base `vectors.usearch` stays untouched. MCP search opens base + all live deltas, merges results. Background compaction job folds deltas into a fresh base periodically.

**Cost when adopted:** Read-path complexity (k-way merge, MCP delta reload signal). Compaction job + scheduling. Vector floats only in usearch unless paired with A1 for durability.

### sqlite-vec migration

**Triggers:**

- usearch ships a regression we can't work around
- Operational pain from the two-file (`vectors.usearch` + `vectors.meta.json`) split exceeds the friction of extension loading

**Status:** Not the first move. The reasons for the original swap away from sqlite-vec (extension loading, HNSW maturity, deploy simplicity) have weakened over time (bundled .so via `pip install sqlite-vec`) but haven't reversed. Re-evaluate if either trigger fires.

## Rejected during this ADR

- **Recommend A1 immediately** (initial proposed version of this ADR). Superseded after modelling the scaling curve and pricing host swap. A1 is the right fix at 10M+ vectors; premature at 1M.
- **Recommend sqlite-vec migration** (my first reaction after seeing the vestigial `vectors_vec` table). Superseded after reading #30, #83, and the original swap rationale. Don't churn a sound decision for marginal gain.
- **Per-process / sidecar rebuild only** (without raising the worker mem). Adds a moving part (sidecar lifecycle, when to fire it) for the same disk + RAM cost as just giving the worker more memswap. Operationally noisier.

## Acceptance criteria

- [x] `086a604d` — interim env gate landed (worker stops OOMing in default config)
- [x] `docker-compose.yml` — `KAIRIX_WORKER_MEMSWAP_LIMIT` hook added (default 1g — no behaviour change for existing deployments)
- [x] `docs/operations/runbooks/worker-memory-and-swap.md` — operator walkthrough to re-enable worker writes with swap headroom
- [x] This ADR records the decision + the trigger conditions for the architectural fallbacks
- [ ] Operator (Dan) applies the runbook on production VM (32 GB swap + 16-32 GB RAM per his note); verifies the worker completes an embed cycle without OOM
- [ ] Preflight `vector-store-vs-content-vectors` drift drops cycle-over-cycle (961k → low single digits within a week)
- [ ] If trigger conditions fire later: adopt A1 / A2 / sqlite-vec per the matching section

## Operational implications

**For existing operators:** No required change. Default `mem_limit: 1g` + default `memswap_limit: 1g` keeps the historical behaviour. Worker will skip usearch writes (via the interim env gate's default-OFF) until they opt in via the runbook.

**For operators with 1M+ vector corpora:** Follow the runbook once. ~10 minutes of work on the VM. Worker resumes vector index writes; cycle takes 2-10 min longer; otherwise transparent.

**For dev:** Don't build A1/A2 speculatively. The interim env gate is the kill switch; the runbook is the operational fix. Architectural work waits for trigger conditions.

## Migration

- **Phase 1 (shipped, `086a604d`):** Interim env gate, default OFF. Worker no longer OOMs in default config.
- **Phase 2 (this ADR + runbook):** Operators on 1M+ corpora apply the runbook. Worker writes resume with swap headroom.
- **Phase 3 (conditional):** A1 / A2 / sqlite-vec — only if matching trigger condition fires. Track via re-opened sub-issues against #335 with the trigger evidence.
