# How To: Read the `mcp_call_log` Per-Call Observability Table

**Purpose:** Investigate brief failures, MCP tool latency tails, and per-tool error rates using the per-call observability surface added in issue #398.

**Background:** Before #398, the FastMCP server logged only `Processing request of type ...` without naming the tool, and failures appeared as terse `WARNING run_brief failed: 1 (of 6) futures TimeoutError` lines in container logs — useless for "which tool is slow?" or "which error class dominates?". The `mcp_call_log` SQLite table now records one row per MCP tool call. Operators query it via `kairix probe mcp-calls`.

---

## Step 1 — Confirm the migration is applied

The table is created in the canonical schema (`kairix/core/db/schema.py`) so fresh deployments receive it automatically. For pre-#398 databases run the in-place migration once:

```bash
# Preview the migration first.
python3 scripts/migrations/2026-06-03-mcp-call-log-schema.py --dry-run

# Apply the migration.
python3 scripts/migrations/2026-06-03-mcp-call-log-schema.py
```

The migration is idempotent; re-running on an already-migrated database is a no-op.

---

## Step 2 — Run `kairix probe mcp-calls`

```bash
# All-time stats across every tool.
kairix probe mcp-calls

# Last hour only.
kairix probe mcp-calls --since 1h

# Just the brief tool.
kairix probe mcp-calls --tool brief

# Machine-readable envelope for piping into jq.
kairix probe mcp-calls --json
```

Text-mode output columns:

| Column      | Meaning                                                            |
|-------------|--------------------------------------------------------------------|
| tool        | MCP tool name (matches the wrapped handler's `__name__`).          |
| count       | Calls that match the filters.                                      |
| p50ms       | 50th-percentile latency in milliseconds.                           |
| p95ms       | 95th-percentile latency.                                           |
| p99ms       | 99th-percentile latency (the operator-actionable tail).            |
| ok%         | Percent of calls that returned a non-error envelope.               |
| top_errors  | Top-3 error classes; empty when ok% is 100.                        |

`--since` accepts `Ns` / `Nm` / `Nh` / `Nd` suffixes (seconds, minutes, hours, days). Without `--since` the report covers the whole table.

---

## Step 3 — Common operator queries

The CLI is a thin projection over `mcp_call_log`; operators investigating an incident often want to query the table directly. The DB lives at the path `kairix.paths.db_path()` resolves — typically `${KAIRIX_DATA_DIR:-/var/lib/kairix}/index.sqlite`.

```bash
DB=$(python3 -c 'from kairix.paths import db_path; print(db_path())')

# Count by tool over the last hour.
sqlite3 "$DB" "
    SELECT tool, COUNT(*)
    FROM mcp_call_log
    WHERE timestamp > datetime('now', '-1 hour')
    GROUP BY tool
    ORDER BY COUNT(*) DESC
"

# Error rate by error_class over the last day.
sqlite3 "$DB" "
    SELECT error_class, COUNT(*)
    FROM mcp_call_log
    WHERE success = 0
      AND timestamp > datetime('now', '-1 day')
    GROUP BY error_class
    ORDER BY COUNT(*) DESC
"

# p99 latency per tool over the last hour
# (approximation — SQLite has no native percentile aggregate).
sqlite3 "$DB" "
    SELECT tool, MAX(latency_ms) AS p100_ms
    FROM mcp_call_log
    WHERE timestamp > datetime('now', '-1 hour')
    GROUP BY tool
"

# Recent failures with payload-hash for correlation.
sqlite3 "$DB" "
    SELECT timestamp, tool, agent, latency_ms, error_class, payload_hash
    FROM mcp_call_log
    WHERE success = 0
    ORDER BY id DESC
    LIMIT 50
"
```

---

## Step 4 — Retention pruning

The table is append-only — every MCP call adds a row. A busy multi-agent team produces ~10k rows/day. Without pruning the table grows unbounded; the file footprint stays small (one row is ~80 bytes) but `MAX(timestamp)` scans and per-tool aggregates slow down over months.

Recommended retention: **30 days** for production deployments. Operators tune this against their incident-investigation lookback.

```bash
# One-shot prune (30-day retention).
DB=$(python3 -c 'from kairix.paths import db_path; print(db_path())')
sqlite3 "$DB" "DELETE FROM mcp_call_log WHERE timestamp < datetime('now', '-30 days')"
sqlite3 "$DB" "VACUUM"
```

For continuous retention, schedule this as a cron entry on the kairix host:

```cron
# /etc/cron.d/kairix-mcp-call-log-prune
0 3 * * * kairix sqlite3 /var/lib/kairix/index.sqlite "DELETE FROM mcp_call_log WHERE timestamp < datetime('now', '-30 days')"
```

If observability lookback is an audit requirement (regulatory or contractual), ship rows to a separate analytics store before pruning — the table is intentionally write-only-from-kairix, so an external collector consumes via `SELECT * WHERE id > <last_seen_id>` and then DELETE-as-you-go.

---

## Step 5 — When the report says "no calls recorded"

The `kairix probe mcp-calls` report shows `mcp-calls: no calls recorded` when the filtered window has zero rows. Three causes:

1. **The MCP server hasn't received traffic since the migration.** Expected on a freshly-deployed instance — call any MCP tool and re-run the probe.
2. **The table is missing.** The probe surfaces an actionable error pointing at the migration script (Step 1).
3. **The `--since` window is too tight.** Drop `--since` to see the full table; if rows exist outside your window, widen the lookback.

---

## What rows the wrapper writes

Every successful or failed MCP tool call appends one row with:

| Column        | Source                                                          |
|---------------|-----------------------------------------------------------------|
| timestamp     | UTC ISO8601 at call completion.                                 |
| tool          | Wrapped handler's `__name__` (e.g. `search`, `brief`).          |
| agent         | The `agent` kwarg if the handler received one; NULL otherwise.  |
| latency_ms    | Wall-clock from call start to result return, integer ms.        |
| success       | 1 if the handler returned an envelope without `error`, else 0.  |
| error_class   | Exception class name on failure; NULL on success.               |
| payload_hash  | SHA-256 of `sorted(kwargs.items())`, first 16 hex chars.        |

The write is fire-and-forget — observability failure NEVER breaks the tool call. If `mcp_call_log` is missing or the DB is unwritable, the wrapper logs a `WARNING mcp_call_log INSERT failed (swallowed)` line and the tool call still returns its envelope to the agent.

---

## Related

- `kairix/agents/mcp/errors.py` — INSERT site (`_record_mcp_call`).
- `kairix/quality/probe/mcp_calls_cli.py` — the probe CLI.
- `scripts/migrations/2026-06-03-mcp-call-log-schema.py` — in-place migration.
- `tests/load/test_mcp_concurrent_brief.py` + `test_mcp_concurrent_search.py` — load tests reproducing the concurrency profile that motivated #398.
