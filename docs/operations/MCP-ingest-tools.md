# MCP ingest tools — `ingest_chat` and `facts_about`

## What this is for

Two MCP tools let agents work with the fact layer directly: `ingest_chat` lets an agent submit a fresh transcript and have it parsed, chunked, and fact-extracted in one call; `facts_about` lets an agent ask "what do we know about this entity?" and get back a structured fact set with evidence citations. Both are namespace-scoped so an agent working on `team-alpha` cannot accidentally read or write facts belonging to `team-beta`.

These tools are the agent-facing surface for the same use cases the operator runs from the CLI (`kairix ingest-chat` and the search pipeline's fact-retriever). The MCP surface adds namespace enforcement, response shaping for token efficiency, and a `health` envelope on every call so agents handle partial degradation gracefully.

## Tool reference

### `ingest_chat`

Submit a transcript for chunking + fact extraction. Returns counts and the operator-facing summary string.

**Arguments:**

| Name | Type | Required | Default | Notes |
|------|------|----------|---------|-------|
| `transcript` | string or list of turn objects | yes | — | Either a JSONL string (one turn per line) or a list of `{role, content, ...}` dicts. The tool normalises both shapes. |
| `namespace` | string | no | `shared` | Namespace scope stamped onto every extracted fact. |
| `window_turns` | int | no | `5` | Sliding-window size for fact extraction. |
| `no_extract` | bool | no | `false` | If `true`, write chunks only — no LLM extraction, no fact persistence. |
| `conversation_id` | string | no | `null` | Override the conversation id used in the chunk filename. Defaults to the transcript's `conversation_id` field or a synthesised stem. |

**Return envelope:**

```json
{
  "ok": true,
  "result": {
    "turns_ingested": 84,
    "conversations_processed": 1,
    "windows_extracted": 17,
    "facts_added": 23,
    "facts_superseded": 2
  },
  "health": {
    "vector_search": "ok",
    "chat": "ok",
    "secrets_loaded": "ok",
    "degraded_reason": null,
    "next_action": null
  }
}
```

When the LLM backend is degraded (cold start, credential failure, rate limit), the tool returns `ok: true` for chunks-only operation and surfaces the degradation in `health.degraded_reason` plus a `health.next_action` instruction. Agents should surface `next_action` to the human operator.

### `facts_about`

Query the fact store for everything known about an entity. Returns a small list of `(attribute, value, confidence, evidence)` rows.

**Arguments:**

| Name | Type | Required | Default | Notes |
|------|------|----------|---------|-------|
| `entity` | string | yes | — | Canonical entity name (kebab-case, e.g. `agent-alpha`). Aliases resolve via the entity graph. |
| `namespace` | string | no | `shared` | Limits facts to this namespace scope plus `shared`. |
| `include_superseded` | bool | no | `false` | If `true`, returns historical facts marked superseded — useful for "how did our decision evolve?" |
| `max_results` | int | no | `25` | Soft cap on returned rows; the tool always returns highest-confidence first. |

**Return envelope:**

```json
{
  "ok": true,
  "result": {
    "entity": "agent-alpha",
    "facts": [
      {
        "attribute": "current-project",
        "value": "project-atlas",
        "confidence": 0.92,
        "evidence_turn_ids": ["s001-t003", "s001-t005"],
        "namespace": "team-alpha",
        "extracted_at": "2026-05-12T09:14:22Z"
      }
    ],
    "count": 1
  },
  "health": { ... }
}
```

When the entity is unknown the tool returns `result.facts: []` plus `result.count: 0` — not an error. Agents that need stricter not-found semantics should check `count == 0`.

## Namespace scoping

Every fact carries a namespace (default `shared`). The MCP tools enforce two rules:

1. **Read fence:** `facts_about(entity, namespace=X)` returns only facts with `namespace ∈ {X, "shared"}`. An agent operating in `team-alpha` cannot peek into `team-beta` by passing the wrong namespace.
2. **Write fence:** `ingest_chat(transcript, namespace=X)` stamps every extracted fact with `namespace=X`. An agent cannot write into a namespace they aren't operating in.

The namespace an agent operates in is set per-session by the MCP server's connection config (operator-controlled). Agents cannot escalate by passing arbitrary namespace strings — the server treats the connection-config namespace as authoritative and rejects calls whose argument doesn't match.

For multi-tenant deployments, run one MCP server per namespace (the standard "one container per namespace" deployment). This matches the deployment fence to the data fence and prevents cross-namespace leakage by construction.

## Safety boundaries

**Both tools are write-or-read against shared state.** `ingest_chat` mutates the chunk store and the fact store; `facts_about` is read-only. Audit logs (when enabled) record the calling agent id, the namespace, the entity, and the resulting fact ids for every call.

**Cost.** `ingest_chat` calls the LLM extractor per window — large transcripts can run hundreds of LLM calls. Agents should ask the operator before ingesting an open-ended transcript; pre-flight with `no_extract: true` first to confirm the shape, then re-ingest with extraction enabled.

**No deletion.** The MCP surface deliberately does not expose a `delete_fact` tool. Contradictions are handled via the consolidation pass (the new fact supersedes the old); operator-driven deletion is a CLI-only path (`kairix entity purge`). This is by design — agents should never be in a position to silently erase ground truth.

**Idempotence.** `ingest_chat` is safe to retry. The chunk write is content-hashed (same body → same hash → skip the disk write); the fact store keys on a deterministic id derived from `(entity, attribute, source_turn_ids)`. An agent's retry after a network blip will not double-count facts.

## Agent-side calling pattern

The recommended flow for an agent that needs to record a conversation:

1. **Pre-flight** with `no_extract: true` to validate the transcript shape and confirm the operator wants the LLM cost.
2. **Confirm namespace** with the operator if the agent doesn't already have it pinned.
3. **Ingest** with extraction enabled.
4. **Verify** by calling `facts_about(<key entity>)` to confirm the expected facts landed.

For an agent that needs to *recall* what's known:

1. Call `facts_about(<entity>)` first — small response, structured.
2. If the fact set doesn't answer the question, call `tool_prep(<topic>)` to fall back to chunk retrieval. The search pipeline already federates facts and chunks, so often `tool_prep` is sufficient on its own.

## Customisation knobs

| Knob | Effect | Where |
|------|--------|-------|
| Per-tool rate limits | Throttle `ingest_chat` so a runaway agent can't burn the LLM budget | MCP server config |
| Audit log target | Where per-call records land (file / syslog / off) | MCP server config |
| Max transcript size | Reject `ingest_chat` calls above this turn count | MCP server config |
| `prompt_template` (server-side) | Custom extractor prompt for this MCP deployment | factory wiring at startup |

The MCP tool maps each underlying use-case knob (window size, namespace, no-extract) through to the corresponding `kairix ingest-chat` CLI flag.

## Troubleshooting

**`ingest_chat` returns `ok: false` with `error: "invalid namespace"`.**

The agent passed a namespace argument that doesn't match its connection-config namespace. `fix:` drop the `namespace` argument — the server uses the connection default. `next:` if the agent legitimately needs to write to a different namespace, route the request through a different MCP server (one container per namespace is the supported model).

**`facts_about` returns an empty fact list for an entity the agent knows exists.**

Either the entity hasn't been ingested into the fact store yet (only the chunk store) or it lives under a different namespace than the agent is querying. `fix:` call the entity-resolution tool (`tool_entity_suggest`) to confirm the canonical name; cross-check against `kairix entity audit`. `next:` if the entity is genuinely there but missing facts, re-ingest the relevant transcript with extraction enabled.

**`ingest_chat` reports `degraded_reason: "llm cold"`.**

The LLM backend is still warming. `fix:` retry in ~5 seconds — the warm-gate envelope across all retrieval/synthesis tools handles cold start automatically; `ingest_chat` surfaces the same degradation. `next:` if degraded persists beyond 30s, see [`runbooks/runbook-vector-search-failure.md`](runbooks/runbook-vector-search-failure.md).

**Agent says `facts_about` returns more rows than expected.**

`include_superseded` defaults to `false`, so historical contradictions are already excluded. `fix:` lower `max_results` (default 25) if the agent's token budget is tight. `next:` if rows look duplicated, the fact-id-collision check failed — check the kairix log for `add(): duplicate id` WARNINGs.

**Agent submits a transcript and `facts_added: 0` despite `windows_extracted > 0`.**

Same diagnosis as the CLI tool: the LLM either returned no facts or returned malformed output. `fix:` re-run with `no_extract: true` to confirm chunks land; if they do, the issue is in the extractor pipeline — see [fact-extractor.md](fact-extractor.md) under "LLM output is malformed."

## See also

- [fact-extractor.md](fact-extractor.md) — what `ingest_chat` runs under the hood
- [eval-suite.md](eval-suite.md) — measuring whether the facts that landed are correct
- [`docs/architecture/fact-layer.md`](../architecture/fact-layer.md) — design ADR
- [MCP-DEPLOYMENT.md](MCP-DEPLOYMENT.md) — base MCP server deployment
- [MCP-CLIENT-MIGRATION.md](MCP-CLIENT-MIGRATION.md) — moving clients off `/sse` onto `/mcp`
- [`docs/user-guide/mcp-tools.md`](../user-guide/mcp-tools.md) — full MCP tools reference
