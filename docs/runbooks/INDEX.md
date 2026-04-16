# Kairix Platform — Runbooks & Procedures Index

Master registry of all kairix operational procedures and incident runbooks.

---

## Quick Links — Something's Wrong

| Symptom | Runbook |
|---|---|
| `kairix search` returns `vec=0, vec_failed=True` | [runbook-vector-search-failure](runbook-vector-search-failure.md) |
| Neo4j auth fails / secrets missing on VM restart | [runbook-secrets-fetch-failure](runbook-secrets-fetch-failure.md) |
| New vault content not appearing in search after 30+ min | [runbook-embedding-lag](runbook-embedding-lag.md) |
| `kairix entity <id>` returns outdated info | [runbook-entity-graph-stale](runbook-entity-graph-stale.md) |
| NDCG@10 dropped after a config or index change | [runbook-benchmark-regression](runbook-benchmark-regression.md) |
| Everything broken / unknown failure | [runbook-emergency-recovery](runbook-emergency-recovery.md) |

---

## All Runbooks — Incident Response

| Runbook | Severity | What it covers |
|---|---|---|
| [runbook-emergency-recovery](runbook-emergency-recovery.md) | P0 | Quick-reference fixes for all common failures, no context needed |
| [runbook-vector-search-failure](runbook-vector-search-failure.md) | P1 | `vec=0, vec_failed=True` — binary symlink bypasses wrapper; credentials not cached |
| [runbook-secrets-fetch-failure](runbook-secrets-fetch-failure.md) | P1 | `/run/secrets/kairix.env` missing on reboot; tmpfs not populated |
| [runbook-embedding-lag](runbook-embedding-lag.md) | P2 | New content not searchable; `kairix-embed.sh` cron check |
| [runbook-entity-graph-stale](runbook-entity-graph-stale.md) | P2 | Entity data outdated; `entity-relation-seed.sh` cron check + re-seed |
| [runbook-benchmark-regression](runbook-benchmark-regression.md) | P2 | NDCG degraded; before/after comparison workflow and rollback |

---

## All Procedures — How-To

| Procedure | What it covers |
|---|---|
| [how-to-restart-services](how-to-restart-services.md) | Safe restart sequence for all kairix-related services |
| [how-to-fix-binary-symlink](how-to-fix-binary-symlink.md) | Fix kairix symlinks to point to the credential-caching wrapper (unblocks vector search) |
| [how-to-upgrade-kairix](how-to-upgrade-kairix.md) | Install tagged release, verify, run onboard check, promote to prod |
| [how-to-onboard-new-installation](how-to-onboard-new-installation.md) | New install checklist: symlinks, GGUF conflict detection, credential method, dimension check |
| [how-to-run-benchmark](how-to-run-benchmark.md) | Benchmark suite, before/after comparison, promotion gate |
| [how-to-debug-search-ranking](how-to-debug-search-ranking.md) | Query intent dispatch, RRF weights, category-specific tuning |

---

## Conventions

- **Incident runbooks** (`runbook-*.md`): Quick symptom → fix. Written for use during an outage — no preamble, direct commands.
- **How-to procedures** (`how-to-*.md`): Step-by-step operational tasks. Include prerequisites, verification, troubleshooting table.
- **Code blocks**: All bash commands in fenced blocks. Include expected output where it helps.
- **Status checks first**: Every runbook starts with the fastest possible check to confirm the failure mode.

---

## Maintenance

| Review | Frequency | Trigger |
|---|---|---|
| Index accuracy check | Monthly | First of month |
| Runbooks vs actual procedures | On kairix version bump | After any cron script deployment |
| Runbook gap scan | After each incident | New failure modes added to backlog |
