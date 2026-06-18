# Runbook — agent-facing query queue (ADR-029 G.1 spike)

Status: introduce — flag-gated OFF by default
Flag: `agent_query_queue`
Tracking: [GH #354](https://github.com/three-cubes/kairix/issues/354)
Spec: [`docs/architecture/ADR-029-agent-query-queue-and-carry-along-delivery.md`](ADR-029-agent-query-queue-and-carry-along-delivery.md)

## What this changes (when ON)

The MCP `tool_search` handler routes through a dispatch-or-queue decorator.

- Fast calls (≤ 1.5 s wall-clock) return the standard search envelope as today.
- Slow calls return a plain-text reply: `"Processing your request (id: q_<hash>). Your answer will be delivered when ready."` — NOT an error envelope. The agent reads it as "accepted, continue".
- The handler keeps running on a background worker thread.
- The next call from the same agent (identified by `Mcp-Session-Id` header, falling back to `X-Kairix-Agent`, then to the process-global `unknown-agent` bucket) receives the completed result back as a `carry_along` prefix on its own envelope.

When OFF (default), `tool_search` runs synchronously exactly as today and the `pending_queries` table stays empty.

## When to flip the flag

Flip ON when you want to validate the carry-along delivery shape in a single deployment slice. The G.1 spike is `tool_search`-only; the other MCP tools stay on the legacy `ColdStart`/`require_ready` envelope until G.2 rolls the same pattern across them.

Pre-flip checklist:

1. Confirm the deployment is running v2026.5.30 or later.
2. Confirm `pending_queries` exists: `sqlite3 /data/kairix/index.sqlite ".schema pending_queries"` should print the table.
3. Snapshot the row count: `SELECT COUNT(*) FROM pending_queries;` — should be 0 before the flip.

Flip:

```bash
# Per docs/architecture/feature-flag-architecture.md §3.4 — env var wins.
export KAIRIX_FEATURE_AGENT_QUERY_QUEUE=true
# OR set in kairix.config.yaml under `features:` and restart the MCP server.
```

Confirm:

```bash
kairix features status | grep agent_query_queue
# Expected: agent_query_queue   true   stage=introduce   source=env (or config)
```

## What telemetry to watch

Until G.3 lands `kairix queue status` + a 24h GC tick, operators run periodic manual SQL checks against the kairix DB. **Rows accumulate without manual cleanup in G.1 — operators run periodic manual SQL cleanup until G.3.**

### Status counters (run during normal operation)

```sql
-- Per-status totals.
SELECT status, COUNT(*) FROM pending_queries GROUP BY status;

-- Oldest in-flight row (catches stuck handlers).
SELECT id, agent_id, submitted_at, started_at
  FROM pending_queries
  WHERE status = 'in_progress'
  ORDER BY submitted_at
  LIMIT 5;

-- Recent failures (catches handler-raise paths).
SELECT id, agent_id, error_message, completed_at
  FROM pending_queries
  WHERE status = 'failed'
  ORDER BY completed_at DESC
  LIMIT 10;
```

### Healthy operation looks like

- `completed` + `delivered` dominate; `delivered` grows as agents make follow-up calls and pull their results.
- `in_progress` rows clear within the slow-handler budget (typically a few seconds for cold-start search; longer for fan-out reranker queries).
- `failed` rows correspond to operator-visible search errors in the worker log.

### Things that warrant escalation

- `in_progress` rows older than 5 minutes — the handler is genuinely stuck; check the worker log for hangs.
- `failed` rate climbing — the carry-along surfaces failures as `error=...` lines, so the agent sees them; investigate the underlying handler error.
- Table growing without bound — G.3's 24h GC isn't live yet; operators run periodic cleanup (see below).

## Manual cleanup until G.3 ships

```sql
-- Delete delivered rows older than 24 hours. Safe to run nightly.
DELETE FROM pending_queries
 WHERE status = 'delivered'
   AND delivered_at < datetime('now', '-24 hours');

-- Optional: clear failed rows older than 7 days once they've been triaged.
DELETE FROM pending_queries
 WHERE status = 'failed'
   AND completed_at < datetime('now', '-7 days');
```

A maintenance-tick GC is the G.3 deliverable that supersedes this manual cleanup. Until then, schedule the `DELETE` above as a cron entry on the kairix VM.

## How to roll back

The flag defaults to OFF; flipping it back is a no-op for the runtime:

```bash
unset KAIRIX_FEATURE_AGENT_QUERY_QUEUE
# OR remove the `agent_query_queue` entry from kairix.config.yaml.
kairix features status | grep agent_query_queue
# Expected: agent_query_queue   false   stage=introduce   source=default
```

The `pending_queries` table remains in the schema (idempotent `CREATE TABLE IF NOT EXISTS`) — no migration required. Existing rows stay until manually cleaned up; the OFF code path never reads from them.

## Related

- ADR-029 — full design including the dispatch-or-queue decision boundary and the Phase 2 MCP notification-push extension (deferred until G.1 dogfood signals it's worth the additional client work).
- `kairix/core/queue/dispatch.py` — the decorator + shared `ThreadPoolExecutor(max_workers=4)`.
- `kairix/core/queue/carry_along.py` — the middleware that flips `completed` → `delivered`.
- `kairix/agents/mcp/server.py::tool_search_queue_aware` — the wrapper that reads the flag and routes between sync and queued paths.
