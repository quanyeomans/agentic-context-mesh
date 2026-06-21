# ADR-029 — Agent-facing query queue + carry-along delivery (replaces cold-start error envelope)

**Status:** Accepted — Wave G.1 implemented (flag-gated, default OFF); waves G.2/G.3 not delivered
**Date:** 2026-05-30 (accepted); G.1 shipped 2026-05-31
**Supersedes:** Cold-start `ColdStart` error-envelope pattern in [`kairix/platform/warm/state.py`](../../kairix/platform/warm/state.py) (introduced GH #320). NOTE: the supersession is *intended*, not yet *effected* — the `ColdStart` path is still the live default and is only replaced for `tool_search` when the `agent_query_queue` flag is ON. See "Implementation status" below.
**Superseded by:** none
**Tracking:** GH #354 (closed NOT_PLANNED 2026-06-18 — the broader cross-tool roll-out was descoped after the G.1 spike; the ADR stands and G.1 is in production behind a default-OFF flag)
**Implementing change:** commit `fc199695` "feat(queue): ADR-029 G.1 spike — pending_queries + dispatch_or_queue + carry-along, applied to tool_search behind agent_query_queue flag (OFF default)" — first released in alpha `v2026.5.31a1`, present in current `main` and stable `v2026.6.18`. There is **no implementing PR** for #354: the G.1 spike landed as a direct-to-main commit (per the trunk-based / direct-to-main workflow for routine work), and #354 was later closed NOT_PLANNED with the cross-tool roll-out descoped — so `fc199695` is the canonical citation, not a PR number. Verified `kairix/core/queue/carry_along.py` + `kairix/core/queue/dispatch.py` present on `main` 2026-06-22.
**Related:** ADR-025 (pipeline observability — queue becomes a new observability surface), GH #320 (the original cold-start visibility issue this ADR supersedes), GH #335 / GH #352 (the operator-side `embed --force` lock contention surfaced during this ADR's investigation)

## Implementation status

| Wave | Status | Evidence |
|---|---|---|
| **G.1** — `pending_queries` schema + `dispatch_or_queue` decorator + `carry_along_prefix` middleware + `agent_query_queue` flag (both-branch tested), wired into `tool_search` only | **Shipped, flag default OFF** | [`kairix/core/queue/dispatch.py`](../../kairix/core/queue/dispatch.py), [`kairix/core/queue/carry_along.py`](../../kairix/core/queue/carry_along.py); schema in [`kairix/core/db/schema.py`](../../kairix/core/db/schema.py); flag in [`kairix/core/features/registry.py`](../../kairix/core/features/registry.py) (`introduced_in="v2026.5.30"`, `default=False`); queue-aware wiring `tool_search_queue_aware` in [`kairix/agents/mcp/server.py`](../../kairix/agents/mcp/server.py); operator doc [`docs/architecture/agent-query-queue.md`](agent-query-queue.md) |
| **G.2** — roll the decorator across the remaining MCP tools (`tool_brief`, `tool_research`, …) + retire `ColdStart` from those paths | **Not delivered** | #354 closed NOT_PLANNED; only `tool_search` is wired |
| **G.3** — `kairix queue status` CLI + `tool_queue_status` MCP tool + 24h GC + scale-bound integration | **Not delivered** | No `queue status` / `tool_queue_status` surface exists |

The `ColdStart` envelope is **still live — confirmed NOT retired** (codebase check 2026-06-22 against `main` / stable `v2026.6.18`). It is the default response shape (flag OFF) for `tool_search` and the only shape for every other tool. The `ColdStart` machinery remains in `kairix/agents/mcp/cold_start.py`, `kairix/agents/mcp/exceptions.py`, `kairix/agents/mcp/transport.py`, `kairix/agents/mcp/server.py`, `kairix/agents/mcp/cli.py`, and `kairix/platform/warm/{state,runner}.py`; the OFF branch of the `agent_query_queue` flag (`kairix/core/features/registry.py`) still routes `tool_search` through it. Retirement (Definition-of-done #11) is therefore **not done** — it stays deferred until the flag is dogfooded ON and the cross-tool roll-out (G.2) lands.

## Context

Today every MCP tool call that hits a not-yet-warm kairix returns a structured `ColdStart` error envelope:

```json
{
  "error": "ColdStart",
  "tool": "tool_search",
  "status": "warming",
  "elapsed_seconds": 2.1,
  "estimated_seconds_remaining": 5.9,
  "guidance": "kairix is warming (one-time cost per process). Retry this call in ~6 seconds."
}
```

The intent (GH #320) was to give the agent a structured "wait + retry" affordance instead of a slow first call. The observed behaviour is that **agents treat the envelope as "kairix is broken" and fall back to in-context reasoning** — the `error` key triggers their fault-tolerance heuristic, the "retry in N seconds" guidance is ignored, and they proceed to answer half-blind from prior knowledge. Operators see the dogfood reports come back with notes like "kairix/memory is down" even when warm-up completed within 7s.

The root issue is the framing, not the wait. From the agent's perspective:

* `{"error": "ColdStart", ...}` reads as "tool failed, recover".
* `"Processing your request (id: q_abc123). Your answer will be delivered when ready."` reads as "tool accepted my request, continue".

The second framing also generalises to slow queries that aren't cold-start cases — reranker cold misses, large fan-outs, busy queues, queries that hit the worker mid-`embed --force`. Any path where the answer takes >N seconds gets the same treatment.

A second observed gap: the agent has to retry. Even if the agent honoured the retry guidance, the retry is the agent's responsibility — they have to track that they had a pending query, count seconds, fire the same tool again. None of today's agent surfaces (Claude in CLI, Claude in chat, custom agents) do this reliably. The solution is to make delivery the server's responsibility.

## Decision

Every MCP tool call routes through a **dispatch-or-queue** decorator with three modes:

1. **Fast synchronous (≤ budget, default 1500 ms)** — current behaviour. The handler runs in-line; the result is returned in the tool response. Today's hot path.
2. **Async-queued with carry-along** — the handler exceeds the budget OR kairix is in a known-slow state (warming, lock contention, embed in flight). Kairix queues the request to a SQLite `pending_queries` table, returns a plain-text `"Processing your request (id: q_<hash>). Your answer will be delivered when ready."` response, and runs the handler in a background worker thread. When the next tool call from the same agent arrives, completed results are prepended to that call's response (the carry-along).
3. **Optional MCP notification push (Phase 2)** — clients that support `notifications/message` get the result pushed as soon as it's ready, without waiting for the next tool call. Falls back to (2) for clients that don't render notifications.

Three principles:

* **Plain text on the queued path, never error envelope.** The agent's heuristic for plain-text response is "ok, proceeding"; for `{"error": ...}` it's "broken, fall back". We are not trying to fix the heuristic; we're using it.
* **Agent never retries.** Server-side delivery via carry-along (and optional push). The agent's only obligation is to keep talking to kairix.
* **Synchronous when fast is possible.** The queue is the fallback for slow paths, not the default — fast queries stay fast.

## Mechanics

### `pending_queries` schema

```sql
CREATE TABLE pending_queries (
    id TEXT PRIMARY KEY,                  -- q_<8-char-hash-of-(agent_id, tool, args, submitted_at)>
    agent_id TEXT NOT NULL,               -- from MCP session-id or X-Kairix-Agent header
    tool TEXT NOT NULL,                   -- e.g. "tool_search"
    args_json TEXT NOT NULL,              -- JSON-serialised args for handler replay
    args_hash TEXT NOT NULL,              -- sha256(tool || canonical-json(args)) for dedup
    status TEXT NOT NULL,                 -- 'pending' | 'in_progress' | 'completed' | 'failed' | 'delivered'
    submitted_at TEXT NOT NULL,           -- ISO8601
    started_at TEXT,                      -- ISO8601, set when worker picks up
    completed_at TEXT,                    -- ISO8601, set when handler returns
    delivered_at TEXT,                    -- ISO8601, set when result returned via carry-along
    result_json TEXT,                     -- handler's return value, JSON-serialised
    error_message TEXT,                   -- truncated handler exception if status='failed'
    UNIQUE(agent_id, args_hash, submitted_at)  -- dedup within a single 60s window
);

CREATE INDEX idx_pending_queries_agent_pending
  ON pending_queries(agent_id, status)
  WHERE status IN ('completed', 'failed');  -- carry-along reads this index per call
```

### `dispatch_or_queue` decorator

```python
@dispatch_or_queue(budget_seconds=1.5)
def tool_search(args, *, agent_id, db):
    ...
```

Implementation shape:
1. Compute `args_hash` + look up an in-flight or recently-completed `pending_queries` row for `(agent_id, args_hash)` within the last 60s. If found → return that row's id + status text (idempotent re-call).
2. Submit the handler to a background `ThreadPoolExecutor` (single shared pool, F66 budget bounds via `per_tick_max_items`).
3. Wait up to `budget_seconds` for completion (using `Future.result(timeout=budget_seconds)`).
4. If the handler returns within budget → write the result to `pending_queries` with `status='delivered'` (so carry-along won't re-deliver), return the result synchronously.
5. If timeout → write `status='in_progress'`, leave the future running. Return plain text: `"Processing your request (id: q_<hash>). Your answer will be delivered when ready."`. The background thread updates the row to `status='completed'` when done.

### Carry-along middleware

Every MCP tool entry runs this BEFORE the handler:

```python
def carry_along_prefix(agent_id: str, db: sqlite3.Connection) -> str:
    rows = db.execute(
        "SELECT id, tool, result_json FROM pending_queries "
        "WHERE agent_id = ? AND status = 'completed' "
        "ORDER BY completed_at LIMIT 5",
        (agent_id,),
    ).fetchall()
    if not rows:
        return ""
    db.executemany(
        "UPDATE pending_queries SET status='delivered', delivered_at=? WHERE id=?",
        [(_now_iso(), r[0]) for r in rows],
    )
    return _format_carry_along(rows)  # "Earlier results now available:\n- [q_abc]: ...\n- [q_def]: ..."
```

The carry-along prefix is prepended to whatever the current tool returns. Cap at 5 completed results per call to bound response size — additional results stay queued for the next call.

### Agent identity

Three sources, in priority order:

1. **MCP session-id** (`Mcp-Session-Id` header per MCP streamable-HTTP spec) — present on every call from a single agent session. Use this as the canonical `agent_id`.
2. **`X-Kairix-Agent` header** — optional explicit override for operators running multi-agent setups that share a session.
3. **Process-global fallback** (`"unknown-agent"`) — only for clients that strip both. Logged as a F21 affordance: "agent identity unset — set `X-Kairix-Agent` to enable carry-along delivery".

### Decision boundary — when to queue vs run synchronously

A tool call gets queued when ANY of:
* `warm/state.py::is_warm()` returns False (cold-start path — primary case)
* The handler exceeds `budget_seconds` (1500 ms default)
* The worker process holds a known-slow lock (e.g. `embed --force` in flight — this is the GH #335/#352 surface that just blocked our verification today)
* Operator override: `kairix.config.yaml` sets `queue.always: true` for a deployment that wants all calls async (rare; some shared-VM deployments may prefer the predictable shape)

A tool call is NEVER queued if:
* The tool is itself a queue-management tool (`tool_queue_status`, etc.) — avoids infinite recursion
* The handler is explicitly marked `@dispatch_or_queue(sync_only=True)` — write tools that must complete before the next read (e.g. ingest)

### Phase 2 — MCP notification push (deferred)

When MCP streamable-HTTP transport is enabled and the client advertises `experimental.serverInitiatedNotifications`, the queue worker can additionally push `notifications/message` to the client as soon as a queued result completes — the agent's chat surface renders the result inline without waiting for the next tool call. Carry-along remains the floor (works with any MCP client); push is the upgrade. Deferred to a follow-up because:

1. Carry-along covers the dogfood pain on its own
2. MCP notification semantics are still maturing across clients (Claude Code: yes; Claude API direct: partial; custom agents: unknown)
3. The Phase 1 carry-along delivers the same outcome with strictly local state

## Fitness functions this work will trip

| Rule | Why it fires | Acceptance |
|---|---|---|
| **F45** (new-capability BDD) | The queue is operator-visible and agent-visible | `tests/bdd/features/agent_query_queue.feature` with cold-start + carry-along happy path |
| **F30** (operator-outcome tests) | `kairix queue status` CLI + `tool_queue_status` MCP tool | Outcome tests asserting stdout shape + envelope content |
| **F53** (status surface) | Operator needs to see pending / completed / failed counts | `kairix queue status` summary mirrors `kairix features status` |
| **F70** (schema-writer symmetry) | New `pending_queries` table | INSERT site in the dispatch decorator; UPDATE site in carry-along middleware; matching schema-bootstrap migration |
| **F66** (per-tick budget) | Background worker is tick-driven (queue drain) | Declare `per_tick_max_items` + `disk_watermark_min_free_bytes` on the queue-worker dataclass |
| **F68** (Protocol failure modes) | Handler timeout, handler raise, agent disconnect mid-queue, duplicate submission | Failure-mode contracts for each |
| **F54** / future F78 (flag both-branch) | The whole queue is flag-gated `agent_query_queue` for safe cutover | OFF (sync-only legacy behaviour) + ON (queue active) both tested |
| **F69** (scale-bound integration) | Carry-along read at 10K+ pending rows | Scale-bound integration test |

## Definition of done

Status reflects stable `v2026.6.18`. G.1 (items 1–7, 10) shipped; the cross-tool roll-out (item 2's "every MCP tool", item 11) and the operator status/GC surface (items 8–9) were descoped when #354 closed NOT_PLANNED.

| # | Criterion | Status / verification |
|---|---|---|
| 1 | `pending_queries` table + writer + reader + ordered-by-submitted-at carry-along read | **Done** — schema in `kairix/core/db/schema.py`; schema-bootstrap test + unit tests |
| 2 | `dispatch_or_queue` decorator implementation + applied to MCP tools | **Partial** — decorator implemented; applied to `tool_search` only (G.1). Remaining tools = G.2, not delivered |
| 3 | Carry-along middleware fires on tool entry, dedups via `status='delivered'` flip | **Done for `tool_search`** — `carry_along_prefix`; sabotage-proof test: drop the middleware → second call doesn't carry first's result |
| 4 | `"Processing your request (id: q_<hash>). Your answer will be delivered when ready."` returned as plain text (NOT error envelope) on queued path | **Done** — `PROCESSING_TEMPLATE`; outcome test asserts plain text, not `{"error": ...}` |
| 5 | Agent identity from MCP session-id with X-Kairix-Agent fallback + process-global fallback with F21 message | **Done** — contract test per identity source |
| 6 | 60s dedup window: identical `(agent_id, args_hash)` within window returns existing job_id | **Done** — `DEDUP_WINDOW_SECONDS`; sabotage-proof test: remove dedup → identical query within 60s queues a second job |
| 7 | `agent_query_queue` feature flag with both-branch tests (F54) | **Done** — default OFF (legacy `ColdStart` envelope path); ON (new queue path) |
| 8 | `kairix queue status` CLI + `tool_queue_status` MCP tool | **Not delivered** (G.3) — no such CLI subcommand or MCP tool exists |
| 9 | Cleanup: `delivered` rows older than 24h get GC'd by a maintenance tick | **Not delivered** (G.3) |
| 10 | `docs/architecture/agent-query-queue.md` operator-facing runbook | **Done** — doc exists |
| 11 | `ColdStart` envelope code path retired (or kept behind the OFF branch of the flag for one release, then removed) | **Not done** — `ColdStart` is still the live default; it is the OFF-branch path for `tool_search` and the only path for every other tool. Retirement deferred until the flag is dogfooded ON and G.2 lands |

## Open decisions

1. **Synchronous budget — 1500 ms or other?** 1500 ms is a guess. Sample some warm-state tool latency distribution from the probe harness and pick a percentile (suggest p95 latency × 1.5). Decide at implementation time.
2. **Cap on completed results carried per call.** Suggest 5; revisit if dogfood shows agents commonly accumulating more pending queries per turn.
3. **Carry-along format.** Markdown? Structured JSON? Plain text? Recommend plain text with one job per line for max compatibility with agent message rendering — `[q_abc123] tool=tool_search args=... result: ...`. Open for design once the first dogfood scenario is captured.
4. **Phase 2 notification push.** Stays deferred per ADR; revisit once Phase 1 dogfood shows the carry-along latency hurts a specific use case (e.g. agent doesn't make another tool call for 30+ seconds).
5. **Lock semantics during `embed --force`.** Today's worker holds an exclusive embed lock during the autonomous embed cycle. Operator-triggered `kairix embed embed --force` blocks waiting for the lock. ADR-029's queue should fire even for operator-side CLI tools that hit the same lock — operators see "Processing your request (id: q_...); embed in flight, will run when lock releases" rather than a blocking 60s wait then error. Scoped as part of the F30 outcome test for `kairix embed`. Cross-references the lock-contention issue surfaced today.
6. **Multi-process visibility.** `pending_queries` in SQLite means the queue is visible to every kairix process (server + worker + CLI). The dispatch decorator runs in the server; the background worker drains in the server's thread pool. If we ever split server/worker across processes, the queue still works (both read the same SQLite). Recommend keeping the worker in-process for v1 (simpler); revisit if process isolation becomes a requirement.
7. **Tool-call cancellation.** If the agent never makes another tool call (session ends), pending queries sit forever. The 24h GC cleans them up, but the *handler* may still be running. Recommend handler-side cooperative cancellation via `Future.cancel()` — needs handler-side awareness. Open.

## Sequencing

Three waves. Each ships independently behind the `agent_query_queue` flag (default OFF until v1 is dogfooded). Status as of stable `v2026.6.18` is in the rightmost column — see "Implementation status" above for evidence.

| Wave | Scope | Status |
|---|---|---|
| **G.1** | `pending_queries` schema + `dispatch_or_queue` decorator + carry-along middleware + `agent_query_queue` flag both-branch tested. Applied to `tool_search` only (proves the pattern). The `ColdStart` envelope stays as the OFF-default path for that tool until cutover. | **Shipped** (commit `fc199695`, `v2026.5.31a1`; flag default OFF) |
| **G.2** | Apply decorator to remaining MCP tools (`tool_brief`, `tool_research`, `tool_classify`, `tool_contradict`, `tool_warm`, etc.). Each migration is one PR; F45/F30 tests per tool. Retire `ColdStart` envelope from those tools' code paths. | **Not delivered** (#354 closed NOT_PLANNED) |
| **G.3** | `kairix queue status` CLI + `tool_queue_status` MCP + operator runbook + maintenance-tick GC (24h delivered cleanup) + F69 scale-bound integration test (10K+ pending rows). | **Not delivered** (operator runbook [`agent-query-queue.md`](agent-query-queue.md) exists; CLI/MCP status surface + GC do not) |

Phase 2 (notification push): separate ADR-030 if Phase 1 dogfood shows the case.

## Why this isn't ADR-028's job

ADR-028 covers chunking strategy + chunking quality evaluation. The queue is orthogonal — it's about request-response semantics, not about how documents are split. Keeping them separate so each ADR is independently shippable.

## Related work

* GH #320 — cold-start visibility (this ADR is *intended* to supersede the response shape; as of `v2026.6.18` the supersession is effected only for `tool_search` under the ON branch of `agent_query_queue` — the `ColdStart` envelope and the underlying warm-tracking machinery both stay)
* GH #335 — worker OOM under `embed --force` (related: today's embed-lock observation belongs in this ADR's "open decision #5")
* GH #352 — `VectorIndex` read_only mode + `clear()` (the operator-side `embed --force` whose verification was blocked by the same lock contention that motivates this ADR's Open decision #5)
* ADR-025 — pipeline observability (the queue is a new observability surface; queue status emit goes through the same `status_emit` plumbing)
* MCP streamable-HTTP spec, server-initiated notifications — Phase 2 prerequisite
