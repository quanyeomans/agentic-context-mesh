# Worker Preflight — Persistence Integrity Audit

The preflight check audits kairix's persistence invariants before the
worker starts processing. It catches the failure mode that surfaced
during the IM-6 cutover: 68,814 active documents shipped to the
dogfood VM with **zero matching FTS rows**, and BM25 silently
degraded to vector-only for 90 minutes before anyone noticed.

## When to run

- **Every deploy / container restart** — the worker invokes the same
  check at boot and logs the result. Run `kairix worker preflight`
  manually if you want a quick "is this VM healthy?" check without
  reading worker logs.
- **Every cutover** — after flipping a feature flag, run preflight
  to confirm the new code path didn't break a write invariant. Pair
  with `scripts/cutover/capture_baseline.py` for the full protocol.
- **After any data migration** — schema migrations, vector index
  rebuilds, or bulk re-extracts can leave the four storage layers
  (documents, content, vectors, FTS) out of sync; preflight surfaces
  the drift.

## Usage

```bash
# Text mode — one line per gap, exit 0 if healthy, 1 if any error gap
kairix worker preflight

# JSON mode — machine-readable envelope for monitoring scripts
kairix worker preflight --json

# Auto-heal — rebuild_fts for documents-without-fts; re-audit
kairix worker preflight --auto-heal

# Audit a specific DB (testing / non-default path)
kairix worker preflight --db-path /path/to/index.sqlite
```

Exit codes:

- `0` — healthy (no error-severity gaps; warn / info gaps may still
  appear in the output for visibility)
- `1` — at least one error-severity gap

## Invariants

| Invariant | Severity | What it means |
|---|---|---|
| `documents-without-content` | error | A row in `documents` has `active=1` but no matching `content` row keyed by `hash`. The document is in the registry but its text is missing. |
| `documents-without-fts` | error | Active document with no `documents_fts` row. **This is the IM-6 failure mode** — BM25 cannot find the document; hybrid search silently degrades to vector-only. |
| `documents-without-vectors` | error | Active document with no `content_vectors` row. The document is not in the embed index; vector search cannot find it. |
| `content-vectors-without-documents` | warn | A `content_vectors` row's `hash` does not appear in `documents`. Orphans accrue when a re-index partially fails; not fatal but wastes index space. |
| `fts-without-documents` | warn | A `documents_fts.rowid` maps to a missing or inactive document. Same orphan shape as above on the BM25 side. |
| `vector-store-vs-content-vectors` | info | usearch vector count vs `content_vectors` count drift beyond a 5% tolerance. Wave 2 emissions can lag a tick; large drift means the disk index is out of sync. |
| `entity-signals-staging-not-stuck` | warn | Entity signals queued for Neo4j but un-pushed for more than 7 days. The drain to Neo4j stalled — investigate the Curator job. |
| `connector-cursors-vs-bronze` | info | Cursors exist in the DB but `kairix.config.yaml` has no matching connector entry. Likely a stale rename. Wave 3 sharpens this check. |

## Reading the output

Each gap line follows this shape:

```
[ERROR] documents-without-fts: count=68814 sample=[note-a.md, note-b.md, ...] — fix: run kairix embed rebuild-fts to repopulate the FTS5 index; next: re-run kairix worker preflight to confirm; run: kairix embed rebuild-fts
```

- **Severity** — `ERROR` is fatal in strict mode; `WARN` and `INFO`
  are visibility-only.
- **Invariant** — kebab-case identifier matching the table above.
- **Count** — how many rows failed this check.
- **Sample** — up to 5 example paths / ids for spot-checking.
- **Remediation** — F21-compliant action text with `fix:`, `next:`,
  and `run:` markers.

## Auto-heal

`--auto-heal` runs `rebuild_fts` for the `documents-without-fts`
gap, then re-runs the audit. It can also prune orphan
`content_vectors` rows into `content_vectors_pruned` through the
maintenance scheduler.

For production, take a database backup or VM snapshot before auto-healing
orphan vectors. Orphan pruning is designed to be safe and idempotent, but
the recovery contract is still backup first, prune second, verify third.
Other gaps need operator action via the per-gap remediation.

For the OpenClaw VM, the normal recovery point is the pre-apply OS-disk
snapshot created by `release-vm-deploy.yml` through the shared
`azure-vm-deploy.yml@v1` workflow. If the workflow cannot create that snapshot,
fix the Azure deploy identity role assignment before running cleanup; do not
fall back to snapshot bypass for production cleanup. A one-off operator snapshot
is acceptable for manual maintenance windows when it is named in the PVT record.

Recommended cleanup sequence after a green deploy:

```bash
kairix worker preflight --json
kairix worker preflight --auto-heal
kairix worker preflight --json
kairix embed
kairix worker preflight --json
```

Only run orphan pruning or USEARCH catch-up after the backup/snapshot step has
completed. If `vector-store-vs-content-vectors` remains high after `kairix embed`,
pause and diagnose the derived USEARCH index rather than pruning more metadata.

### USEARCH parity after force rebuild

`content_vectors` is SQLite metadata; the vector bytes live in USEARCH. For the
OpenClaw/Kairix VM, alpha deploys create `docker-compose.kairix-vm-ops.yml` in
the active `/etc/kairix` compose root so `KAIRIX_WORKER_WRITES_VEC_INDEX` is
enabled for the kairix container. Verify the gate before a catch-up run:

```bash
cd /etc/kairix
docker compose -f docker-compose.yml -f docker-compose.kairix-vm-ops.yml \
  exec -T kairix python3 -c 'from kairix.paths import worker_writes_vec_index; print(worker_writes_vec_index())'
```

If the output is `False`, do not expect `kairix embed embed --force` to rebuild
USEARCH; it will only rebuild SQLite `content_vectors`. Fix the compose overlay
or deploy `apply-alpha.sh`, then recreate the container and rerun the force
embed.

If preflight reports `usearch > content_vectors` after a force rebuild, check
for duplicate active document hashes:

```bash
docker compose -f docker-compose.yml -f docker-compose.kairix-vm-ops.yml exec -T kairix python3 - <<'PY'
import sqlite3
from kairix.paths import db_path
db = sqlite3.connect(str(db_path()))
print(db.execute("SELECT COUNT(*) FROM documents WHERE active = 1").fetchone()[0])
print(db.execute("SELECT COUNT(DISTINCT hash) FROM documents WHERE active = 1").fetchone()[0])
PY
```

The embed pipeline de-duplicates chunk identities by `(hash, seq)` before
provider calls and USEARCH writes. A deployed image with that fix should rebuild
USEARCH to match SQLite, then `kairix worker preflight --json` should no longer
surface `vector-store-vs-content-vectors` beyond the 5% tolerance.

## Worker boot integration

The worker calls `_run_preflight_at_boot()` immediately before the
first embed cycle. Default behaviour:

- **Healthy** → log `INFO worker: preflight integrity check passed`
  and continue.
- **Unhealthy (error gaps)** → log `WARNING worker: preflight
  integrity check found N gaps`, log one warning line per gap, and
  continue. The worker does NOT crash on first boot of a degraded VM
  because crashlooping is worse than visibility.
- **Unhealthy + `KAIRIX_PREFLIGHT_STRICT=1`** → log + exit non-zero
  so a supervised container restart loop will surface the problem.

Set `KAIRIX_PREFLIGHT_STRICT=1` on canary / staging boxes where
crashlooping is the desired signal. Leave it unset on shared dogfood
infra where visibility-without-downtime is more important.

## Common workflows

**A deploy lands and dogfood says "search results look thin"**

```bash
kairix worker preflight
# If documents-without-fts: count > 0 — that's the IM-6 mode
kairix worker preflight --auto-heal
# Re-test the failing query
```

**A flag flip rolled out; verifying the new write path**

```bash
# Pre-flip
kairix worker preflight --json > /tmp/preflight-before.json
# Flip the flag, run a representative workload
# Post-flip
kairix worker preflight --json > /tmp/preflight-after.json
diff /tmp/preflight-before.json /tmp/preflight-after.json
```

**Container healthcheck**

```yaml
healthcheck:
  test: ["CMD", "kairix", "worker", "preflight"]
  interval: 5m
  timeout: 30s
  retries: 3
```

## Implementation

- Module: [`kairix/core/db/integrity.py`](../../../kairix/core/db/integrity.py)
- CLI: [`kairix/worker_cli.py`](../../../kairix/worker_cli.py)
- Boot wiring: [`kairix/worker.py`](../../../kairix/worker.py) —
  `_run_preflight_at_boot()` called from `main()` before the first
  embed cycle.
- Contracts: [`tests/contracts/test_integrity_invariants.py`](../../../tests/contracts/test_integrity_invariants.py)
- BDD: [`tests/bdd/features/worker_preflight.feature`](../../../tests/bdd/features/worker_preflight.feature)
- F30 outcome test: [`tests/integration/test_worker_preflight_cli.py`](../../../tests/integration/test_worker_preflight_cli.py)
