# Worker memory + swap — re-enabling usearch writes after #335

**Audience:** operators running the kairix worker container on a single VM with bounded RAM.
**Symptom:** worker container restart-loops, `docker logs app-kairix-worker-1` shows `vec_index: converting immutable index to mutable (...)` followed by an immediate restart, vector index drifts further behind `content_vectors` every cycle.
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

Then `docker compose up -d kairix-worker` to apply.

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
docker compose up -d kairix-worker
```

Verify the new limits took effect:

```bash
docker inspect app-kairix-worker-1 \
  --format '{{.HostConfig.Memory}} {{.HostConfig.MemorySwap}}'
```

Expected: `8589934592 17179869184` (bytes — 8g, 16g).

## Step 4 — Watch the first embed cycle

Tail the worker log:

```bash
docker logs -f app-kairix-worker-1
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
watch -n 2 'free -h && echo --- && docker stats --no-stream app-kairix-worker-1'
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
docker exec app-kairix-worker-1 kairix worker preflight 2>&1 | grep vector-store
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
| usearch index file corrupts | Re-embed from scratch (`kairix embed --force`) OR adopt ADR-023 A1 so SQLite is the source of truth |

ADR-023 is the architectural fallback if any of those scenarios fire. Until then, the swap-first operational approach has lower risk and lower lift.

## Related

- [#335](https://github.com/three-cubes/kairix/issues/335) — root-cause issue
- [ADR-023](../../architecture/ADR-023-vector-index-write-architecture.md) — architectural fallbacks if swap approach proves insufficient
- [ADR-019](../../architecture/ADR-019-compose-resource-governance.md) — the original cgroup ceilings; this runbook tunes them per host
- [integrity-and-preflight](./integrity-and-preflight.md) — what each preflight check means
