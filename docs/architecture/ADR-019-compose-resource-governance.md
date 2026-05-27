# ADR-019 — Resource governance at the docker-compose layer

**Status:** Accepted 2026-05-28
**Issues:** #330 (P0 hotfix landed 2026-05-28); 2026-05-27 dual saturation incidents
**Related:** ADR-020 (per-tick budget — application-layer ceiling that pairs with this infrastructure-layer ceiling); ADR-018 (DLT connector framework — the layer this protects)

## Context

On 2026-05-27 the production VM was rendered unreachable twice. The morning incident saw the kairix-worker container drive `sdb` to 96% util for hours via a full-resync-every-tick bug. The evening incident saw the `kairix.service` systemd unit's `Restart=always` + `--remove-orphans` shape compete with a manual `docker compose up -d` and churn containers at 100% CPU. Both required Azure portal hard-stop to recover because the host's Azure agent + cloudflared tunnel could no longer respond.

Both incidents share a structural property: **the kairix-worker container could consume 100% of the host's CPU, memory, or disk IO with no upper bound declared anywhere in the shipped configuration**. Different code-level bugs triggered the saturation each time, but the shared failure mode was that any code-level bug — present or future — could take down the host.

Application-layer ceilings (ADR-020 — per-tick budget + disk-watermark) bound the *intended* work per tick. They do not bound *misbehaviour*: a runaway loop, a memory leak, an unbounded retry, an oversized log buffer can all defeat application-layer ceilings. Infrastructure-layer ceilings are the defence-in-depth that catches the cases application-layer ceilings miss.

## Decision

Every container shipped in the kairix docker-compose stack declares **CPU, memory, and blkio_weight ceilings** sized so the sum across all containers stays strictly below host capacity. The remaining headroom (typically 5-15% of CPU + 1-2 GiB of memory) is reserved for the OS, the Azure VM agent, the cloudflared tunnel, and any operator-side diagnostic tooling.

Ceilings are env-overrideable so operators on larger hosts can scale up without forking the compose file, but the **defaults are sized for the smallest deployment shape we test against** (4-vCPU / 8-GiB host).

### Concrete defaults (4-vCPU / 8-GiB host)

| Service | CPU cap | Memory cap | blkio weight | Rationale |
|---|---|---|---|---|
| `kairix` (MCP server) | 1.5 | 3 GiB | 500 (default) | Agent-facing path; CPU sized for concurrent /mcp calls; mem sized for rerank model load. |
| `kairix-worker` | 1.5 | 1 GiB | **100** (de-prioritised) | Background work; mem sized for worker steady-state RSS ~150 MiB + 6× headroom. blkio 100 means the worker NEVER starves neo4j or kairix-1 for disk during ingest. |
| `neo4j` | 0.75 | 2 GiB | 500 (default) | Knowledge graph; CPU sized for steady-state entity-graph writes + healthcheck. |
| **Sum** | **3.75** | **6 GiB** | — | Leaves ~0.25 CPU + ~2 GiB for OS, Azure agent, cloudflared, operator shells. |

### Env override pattern

Every ceiling is `${KAIRIX_<SERVICE>_<RESOURCE>:-<default>}` so operators on 8-vCPU / 16-GiB hosts set `KAIRIX_WORKER_CPUS=3.0`, `KAIRIX_MCP_MEM_LIMIT=6g`, etc. in `.env` and the compose stack scales without a file fork.

### What's NOT in scope

- **CPU and memory limits are hard caps**, not requests/reservations. A container saturating its own cap is acceptable; the host remaining responsive is the invariant.
- **blkio_weight is a soft priority** (cgroup v1/v2 weight), not a hard IOPS cap. Sufficient to prevent the worker starving neo4j; insufficient to bound absolute throughput. If a future incident shows weight isn't enough, we add `device_read_bps` / `device_write_bps` per-device caps.
- **Network ceilings** are not in this ADR. Network saturation has not been observed as a host-takedown vector.

## Alternatives considered

**A. No infrastructure ceilings; rely on application-layer ceilings only.**
Rejected. Application-layer ceilings (ADR-020) are correctness ceilings for *intended* work. They don't catch leaks, infinite loops, oversized logs, or rogue libraries. The dual-layer pattern (app ceiling + infra ceiling) is industry-standard for production container deployments — both incidents on 2026-05-27 would have been caught at the infra layer alone, before any of the application-layer fixes shipped.

**B. Per-resource quotas via systemd slices.**
Rejected. Compose-layer limits travel with the shipped artefact + are visible to every operator. systemd slices require host-side configuration that operators discover only after the first incident.

**C. Kubernetes resource requests/limits.**
Out of scope. kairix's deployment target is docker-compose on a single VM; k8s adoption is not on the roadmap.

**D. Cgroup-v2 `io.max` for hard IOPS caps.**
Deferred. blkio_weight (soft priority) is sufficient for the observed failure modes. If a future incident demonstrates absolute IO saturation despite weight, this ADR is revisited with `device_read_bps` per-device caps as a follow-up.

## Acceptance criteria

- [x] `docker-compose.yml` declares CPU + memory + blkio_weight for kairix, kairix-worker, neo4j (commit `b2ea4936`)
- [x] Defaults sized for 4-vCPU / 8-GiB host with `KAIRIX_*` env overrides (commits `b2ea4936`, `bf34f83a`)
- [ ] `docs/operations/OPERATIONS.md` documents the override pattern + when to tune
- [ ] BDD scenario `tests/bdd/features/container_resource_cap_enforcement.feature` — load-generator saturates worker to its CPU cap; assert host SSH responds + neo4j healthcheck stays green + kairix-1 keeps serving MCP. *(ADR-020 acceptance criterion lives here too.)*
- [ ] Runbook entry under `docs/operations/runbooks/` for "host unreachable during heavy worker load" symptom → check `docker stats` against caps + tune env

## Operational implications

**Operators upgrading from pre-v2026.5.28** inherit the new caps automatically on `docker compose pull && up -d`. Hosts smaller than 4-vCPU / 8-GiB MUST set lower `KAIRIX_*` env values before bringing up the stack — there is no auto-detect.

**Performance impact**: under steady-state load, no observable difference (containers operate well under cap). Under peak burst (recovery resync, embed backlog), the worker is throttled at 1.5 CPU instead of being able to consume the whole host. Throughput-per-tick stays at ~10 items/min for the SharePoint recovery scenario (vs ~10 items/min observed pre-cap — the cap was never the bottleneck for the actual workload, the per-item extract latency was). The cap *only* engages when the workload would otherwise have saturated the host.

## Pairing with ADR-020

ADR-019 (this document) is the **infrastructure ceiling**. ADR-020 is the **application ceiling** (per-tick item budget + disk-watermark gate). Both are required:

- ADR-019 alone: app would still try to do unbounded work; just slowly. Worker takes 14h to drain a recovery resync at its CPU cap.
- ADR-020 alone: app stops at the budget per tick, but a leak or runaway loop within the budget can still saturate the host.
- Both together: app does bounded work per tick; infrastructure catches the case where app intent ≠ app reality.

## Migration

Live deployment migration was completed during the 2026-05-27 incident response (hotfix landed in `b2ea4936`). No additional migration needed for new deployments — the shipped `docker-compose.yml` carries the caps from v2026.5.28 onwards.
