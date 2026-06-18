# Worker memory + swap — re-enabling usearch writes after #335

**Audience:** operators running the kairix worker container on a single VM with bounded RAM.
**Symptom:** worker container restart-loops, `docker logs app-kairix-1` shows `vec_index: converting immutable index to mutable (...)` followed by an immediate restart, vector index drifts further behind `content_vectors` every cycle.
**Underlying cause:** [#335](https://github.com/three-cubes/kairix/issues/335) — usearch HNSW's incremental-add path needs the full graph resident; at 1M+ vectors that's ~6-8 GB which exceeds the worker's cgroup `mem_limit` (default 1 GB per ADR-019).

This runbook gets the worker writing to usearch again without changing code or schema — by giving the cgroup enough RAM + swap to hold the mutable index.

## When to follow this

- Production has 1M+ vectors in `content_vectors`
- Worker is in restart loop with the symptom log line above
- Host has at least 8 GB RAM total **and** at least 8 GB swap on SSD (verify steps below)
- You want vector search on new content to stay current (no manual rebuilds)

If the corpus is under 500k vectors and you don't see the restart loop, you don't need this runbook — the default `mem_limit: 1g` is fine.

## What you'll change

- `KAIRIX_WORKER_MEM_LIMIT` — raise from 1g to 8g (cgroup RAM ceiling)
- `KAIRIX_WORKER_MEMSWAP_LIMIT` — raise from 1g to 16g (allows the worker to spill into host swap up to 8 GB beyond mem_limit)
- `KAIRIX_WORKER_WRITES_VEC_INDEX` — set to `1` (re-enables the worker write path that #335's interim gate disables)

Then `docker compose up -d kairix` to apply.

## Step 1 — Verify host has enough RAM + swap

SSH to the VM:

```bash
ssh <your-kairix-host>
```

Check RAM and swap:

```bash
free -h
```

Expected output:

```
              total        used        free      shared  buff/cache   available
Mem:          15Gi        ...         ...          ...        ...          ...
Swap:         8.0Gi       ...         ...
```

If `Swap:` shows `0B`, configure swap before proceeding:

```bash
# Create an 8 GB swap file on /data (SSD)
sudo fallocate -l 8G /data/swapfile
sudo chmod 600 /data/swapfile
sudo mkswap /data/swapfile
sudo swapon /data/swapfile

# Persist across reboot
echo '/data/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

Verify:

```bash
swapon --show
free -h
```

Headroom check: with worker raised to `mem_limit: 8g`, kairix-1 at 3g, neo4j at 2g, the cgroups sum to 13g. With the host at 16 GB RAM that leaves ~3 GB for OS + sidecars (openclaw etc). The worker spilling into swap doesn't compete with the other cgroups' RAM — swap is a separate pool.

## Step 2 — Update the kairix env on the VM

The compose env file is `/opt/kairix/.env` (or wherever your stack reads env from — check `docker compose config | grep env_file`).

Edit it to add:

```bash
# Worker memory ceiling — see docs/operations/runbooks/worker-memory-and-swap.md
KAIRIX_WORKER_MEM_LIMIT=8g
KAIRIX_WORKER_MEMSWAP_LIMIT=16g

# Re-enable the worker's in-process usearch write path (issue #335 interim
# gate added 2026-05-28). Default is OFF; set to 1 once the cgroup +
# swap above are sized for the current corpus.
KAIRIX_WORKER_WRITES_VEC_INDEX=1
```

## Step 3 — Recreate the worker container

```bash
cd /opt/kairix
docker compose up -d kairix
```

Verify the new limits took effect:

```bash
docker inspect app-kairix-1 \
  --format '{{.HostConfig.Memory}} {{.HostConfig.MemorySwap}}'
```

Expected: `8589934592 17179869184` (bytes — 8g, 16g).

## Step 4 — Watch the first embed cycle

Tail the worker log:

```bash
docker logs -f app-kairix-1
```

You should see:

```
kairix worker starting — embed every 3600s, ...
worker: preflight integrity check ...
embed pipeline starting ...
Embedding NNNN chunks across NNNN documents (batch_size=250)
vec_index: converting immutable index to mutable (NNNN vectors)   ← used to die HERE
[time passes — minutes, not seconds]
usearch: saved index with NNNN vectors                            ← success
```

The "converting immutable to mutable" step is the previous death point. With swap configured, the worker will fault pages from disk during the conversion; it's slow (typically 2-10 minutes depending on corpus size and SSD speed) but does not OOM.

In a separate terminal, watch memory + swap usage on the host:

```bash
watch -n 2 'free -h && echo --- && docker stats --no-stream app-kairix-1'
```

What's healthy:

- `MEM USAGE` on the worker can grow up to its 8g limit
- Host `Swap: used` rises during the embed cycle, falls after
- No container restart

What's a problem:

- Worker still restarts (mem_limit too low for current corpus — try 12g)
- Host swap fills to 100% — back off; lower mem_limit or add more swap
- Disk IO saturates (`iostat -x 2` shows `%util` pegged at 100% for minutes) — page-fault rate too high, swap on slow disk; either move swap to faster SSD or reduce embed `batch_size`

## Step 5 — Confirm usearch is catching up

After one successful embed cycle, check the preflight drift:

```bash
docker exec app-kairix-1 kairix worker preflight 2>&1 | grep vector-store
```

Expected:

```
[info] vector-store-vs-content-vectors count=NNNN — ...
```

The `count` should DROP cycle-over-cycle as the worker catches up. At the current backlog (~961k vectors behind), expect several embed cycles to fully reconcile.

## When to revisit

| Scenario | Action |
|---|---|
| Corpus passes 5M vectors (~32 GB resident) | Raise `KAIRIX_WORKER_MEM_LIMIT` to 16g, swap to 32g |
| Corpus passes 10M vectors (~64 GB resident) | Outgrown a 16 GB VM — move worker to a larger host OR adopt ADR-023 A1 (BLOB + offline rebuild) to keep the worker bound |
| Embed cycle wall-clock exceeds the schedule (default 3600s) | Worker can't keep up with embedding workload; either reduce embedding source rate, lengthen the cycle, or move to ADR-023 A2 (delta segments) for fresh-write path |
| usearch index file corrupts | Re-embed from scratch (`kairix embed embed --force`) OR adopt ADR-023 A1 so SQLite is the source of truth |

## #352 — VectorIndex read/write modes (post-2026-05-30)

As of v2026.5.30a1's #352 fix, `VectorIndex` opens in one of two modes:

| Mode | Used by | What it does | Memory cost |
|---|---|---|---|
| `read_only=True` | MCP server, eval, probe, recall-check, search-side singleton (`get_vector_index`) | `Index.restore(view=True)` — mmap'd, pages loaded on demand | Near-zero baseline; grows with query working set |
| `read_only=False` (default) | Worker embed cycle (`_open_usearch_index` in `kairix.core.embed.embed`), `--force` rebuilds, ADR-028 re-chunk-sweep | `Index.restore(view=False)` — full HNSW graph loaded into process memory at `.load()` time | ~vectors × 1536 × 4B + HNSW graph overhead (typically 2-4× the vector size) |

**Why this matters for memory tuning:**
- The convert-on-first-mutation path (the original #335 / #352 OOM cause) is gone. The worker no longer needs swap headroom to absorb a one-time conversion spike — but it DOES need permanent resident memory equal to the full mutable index.
- For 1.27M × 1536 vectors that's ~8 GB just for the vectors, plus ~16-32 GB for the HNSW graph (usearch's M16 default). With the runbook's recommended `KAIRIX_WORKER_MEM_LIMIT=8g + KAIRIX_WORKER_MEMSWAP_LIMIT=16g`, the worker may spill into swap during normal operation — slower per cycle but stable.
- `kairix embed embed --force` does NOT need to load the old index at all now — the embed pipeline calls `vec_index.clear()` after `DELETE FROM content_vectors` so the on-disk file is removed and the worker rebuilds from empty. No more OOM during `--force`.

**Memory budget by corpus size (read-write worker mode):**

| Vectors | Resident vectors (×1536×4B) | + HNSW graph (~2.5x) | Recommended `MEM_LIMIT` | Recommended `MEMSWAP_LIMIT` |
|---|---|---|---|---|
| 100k | 0.6 GB | ~2 GB | 4 GB | 8 GB |
| 500k | 3 GB | ~10 GB | 8 GB | 16 GB |
| 1M | 6 GB | ~20 GB | 8 GB | 24 GB |
| 2M | 12 GB | ~40 GB | 16 GB | 48 GB |
| 5M+ | 30 GB+ | ~100 GB+ | Outgrown swap-first — adopt ADR-023 A1 (BLOB + offline rebuild) |

The MCP-server / search-side processes use `read_only=True` and do NOT need this headroom; they sit on the mmap.

ADR-023 is the architectural fallback if any of those scenarios fire. Until then, the swap-first operational approach has lower risk and lower lift.

## `--parallel N` — concurrent batches during catch-up cycles

`kairix embed embed --parallel N` runs up to `N` Azure embed batches concurrently using a `ThreadPoolExecutor`. The Azure call (the ~1-2s/batch network wait) parallelises across threads; SQLite writes and the usearch `add_vectors` call stay serialised under a single-writer lock, so SQLite thread-safety and the single-writer usearch contract are preserved.

Throughput rises roughly linearly with `N` up to Azure's per-deployment quota, then plateaus. Memory also rises linearly because each in-flight batch's text payload and returned vector array are held in process memory until the writer drains them.

| `--parallel` | Throughput vs serial | Extra memory at peak | When to use |
|---|---|---|---|
| `1` (default) | 1x | 0 | Normal incremental embed cycles |
| `3` | ~3x | ~2x batch payload | Recommended for the default VM size (8 GB RAM, 1.27M-vector corpus) |
| `5` | ~4-5x | ~4x batch payload | Mid-catch-up cycle on a worker sized per "Memory budget by corpus size" table above |
| `10` (max) | ~6-8x (Azure-quota-bound) | ~9x batch payload | Large catch-up only; verify Azure quota headroom in the portal first |

`--parallel` above 10 is rejected at the CLI boundary with a pointer back here.

**When to raise `--parallel`:**
- Catch-up cycle wall-clock is unacceptable (e.g. the operator pain from 2026-05-30: a 2.18M-chunk catch-up at serial rate takes ~6 hours; `--parallel 5` brings it to ~1-1.5 hours).
- Worker `MEM USAGE` (`docker stats`) has at least 2-3 GB headroom under its `mem_limit`.
- Azure deployment shows < 50% TPM (tokens-per-minute) utilisation in the portal during the current run.

**When NOT to raise `--parallel`:**
- During normal incremental cycles (the queue is small enough that serial finishes in seconds).
- When Azure is returning 429s (back off to `--parallel 1` until the deployment quota is raised).
- When the worker is already at `mem_limit` (raise the limit first per Step 2 above).

## Related

- [#335](https://github.com/three-cubes/kairix/issues/335) — root-cause issue
- [ADR-023](../../architecture/ADR-023-vector-index-write-architecture.md) — architectural fallbacks if swap approach proves insufficient
- [ADR-019](../../architecture/ADR-019-compose-resource-governance.md) — the original cgroup ceilings; this runbook tunes them per host
- [integrity-and-preflight](./integrity-and-preflight.md) — what each preflight check means
