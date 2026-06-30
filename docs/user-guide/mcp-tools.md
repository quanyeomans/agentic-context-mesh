# Kairix MCP Tools

Kairix exposes its full capability surface via MCP (Model Context Protocol). Any MCP-compatible agent or IDE can use these tools to search, research, look up information, record what it learns, and inspect the running server's setup. The server registers **38 tools** in total; this reference documents every one, grouped by job.

Every tool response carries a `health` envelope (`vector_search` / `bm25` / `chat` / `secrets_loaded` / `degraded_reason` / `next_action`) so an agent can tell what's online before it trusts a result. Tools that touch a cold pipeline may return an `error_code=KAIRIX_COLD_START` envelope with a `retry_after_ms` — when you see that, wait and retry the same call rather than answering from memory.

Every retrieval and synthesis result row carries a source link you can open — the same kind of pointer on every tool — so you and your agents can trace any answer back to where it came from and re-open the original.

The groups below map to how you'll actually reach for them:

- **Retrieval + synthesis** — what agents call to find answers (`search`, `research`, `entity`, `prep`, `timeline`, `contradict`, `brief`, `bootstrap`, `expand`).
- **Agent memory + recall** — what agents call to write back and introspect knowledge (`memory_write`, `ingest_chat`, `facts_about`, `entity_suggest`, `entity_validate`).
- **Help + discovery** — finding the right surface (`usage_guide`, `capabilities`, `recommend_capabilities`).
- **Setup + diagnostics** — operator-facing health and config checks (`onboard_check`, `onboard_scan`, `onboard_agent`, `doctor_check_all`, `doctor_check_agent`, `worker_status`, `features_status`, `secrets_verify`, `dead_letter_status`, `caches_status`, `warm`, `probe_search`, `maintenance_analyze`).
- **Operator-only escalations** — heavy or mutating operations that return an escalation envelope pointing your operator at the matching CLI command (`benchmark_run`, `soak_run`, `probe_burst`, `probe_config`, `embed`, `embed_rebuild_fts`, `store_crawl`, `cc_pair`).

## Retrieval + synthesis tools

These are what agents use to find answers.

### search

Find answers in your knowledge base. Just pass your question — the system handles date-based queries, budget sizing, and entity detection automatically. Call this proactively at session start and whenever a question touches the team's history.

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `query` | Yes | — | Your question |
| `agent` | No | None | Agent name for collection scoping |
| `scope` | No | "shared+agent" | Which collections to search: "shared", "agent", or "shared+agent" |
| `budget` | No | 3000 | Token budget for the result set |
| `limit` | No | 10 | Maximum number of results |

**Returns:** Ranked results with file paths, relevance scores, content snippets, and token counts. Each result carries a source link you can open.

**When to use:** Most questions. This is the default tool for finding information.

---

### expand

Pull the text around a search hit. A search result points at one part of a document; `expand` returns that part plus the parts on either side of it — the section it belongs to — up to a token budget. It reads the surrounding context straight from the index, so you don't have to re-read or re-ingest the whole file.

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `source_uri` | Yes | — | The source link from the search hit you want to expand |
| `seq` | Yes | — | The hit's position marker within its document (the `seq` on the search result) |
| `token_budget` | No | 2000 | How much surrounding text to pull, in tokens |

**Returns:** The matched part plus its neighbouring parts, in reading order, each with its own openable source link and a token count. The matched part is always included, even if it alone is over the budget.

**When to use:** Right after a search hit, when the snippet isn't enough and you need the surrounding context — what comes before and after — to answer with confidence.

---

### research

Ask a complex question that needs more than a single search pass. The system searches multiple times, refining its approach until it finds a good answer or reports what's missing.

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `query` | Yes | — | Your research question |
| `agent` | No | None | Agent name for scoping |
| `max_turns` | No | 4 | Maximum search rounds before stopping |

**Returns:** A synthesised answer with sources cited, confidence score (0-1), list of knowledge gaps if any.

**When to use:** Complex questions, multi-part queries, or when a simple search doesn't return enough. Takes longer but finds more.

---

### entity

Look up a specific person, company, or topic by name. This is a direct lookup from the knowledge graph (Neo4j) — faster than searching.

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `name` | Yes | — | The entity name to look up |

**Returns:** Entity details: name, type, summary, vault file path.

**When to use:** When you know the exact name of a person, company, or concept and want its profile.

---

### prep

Get a quick summary of a topic before committing to a full search. Cheaper and faster than search — good for context checks.

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `query` | Yes | — | The topic to summarise |
| `agent` | No | None | Agent name for scoping |
| `tier` | No | "l0" | "l0" for 2-3 sentences, "l1" for a structured overview |
| `scope` | No | "shared+agent" | Which collections to draw from |

**Returns:** A brief summary with token count.

**When to use:** Quick context checks, deciding whether to do a full search, getting a baseline understanding of a topic.

---

### timeline

Date-aware retrieval for questions that depend on timing. Also useful for debugging how a date-related question will be interpreted — date handling is automatic on `search`, so you don't need to call this first.

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `query` | Yes | — | A question with date references |
| `anchor_date` | No | today | ISO date to anchor relative references |
| `agent` | No | None | Agent name for scoping |
| `scope` | No | "shared+agent" | Which collections to search |

**Returns:** The rewritten query with explicit dates, the detected time window, and date-aware results.

---

### contradict

Call before writing new facts to check for contradictions against existing knowledge. Prevents an agent from recording something the store already disagrees with.

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `content` | Yes | — | The new claim(s) you're about to record |
| `agent` | No | None | Agent name for scoping |
| `top_k` | No | 5 | How many existing records to compare against |
| `threshold` | No | 0.45 | Similarity threshold for flagging a conflict |
| `top_claims` | No | 3 | How many candidate claims to extract from `content` |
| `scope` | No | "shared+agent" | Which collections to compare against |

**Returns:** Detected contradictions with the conflicting prior claims and their sources.

**When to use:** Right before `memory_write` or any other fact-writing step.

---

### brief

Get a synthesised view of a topic — kairix runs a small research loop across the knowledge store and returns a structured briefing. Use it when you'd otherwise be tempted to summarise from memory.

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `agent` | Yes | — | Agent name the briefing is for |

**Returns:** A structured briefing (content + the on-disk path it was written to). The briefing ends with a `## Sources` footer listing the sources behind it, and the response also carries a `sources` field with the same citations as openable source links — so you can check the work behind any part of the summary.

**When to use:** When you want a current synthesised view of where things stand instead of recalling from memory.

---

### bootstrap

Call at session start or whenever you switch topics. Returns your agent role, current board, recent memory, and active goals — it orients you in the team's current state.

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `agent` | Yes | — | Agent name to orient |
| `max_memory_days` | No | 3 | How many days of recent memory to include |

**Returns:** An orientation envelope: role, board, recent memory, active goals, and a `health` field. If `health.vector_search != "ok"`, surface that to your human.

**When to use:** First call of every session; also after switching topics.

---

## Agent memory + recall tools

These let an agent write back what it learns and introspect the knowledge store. They touch persistence and (for some) the LLM extractor, so they only run against a warm pipeline.

### memory_write

Save a memory for an agent. Writes the text as a dated markdown file in the agent's memory folder inside the knowledge store, and indexes it right away so search finds it in the same session. (Shipped v2026.6.18 as the agent-facing equivalent of the `kairix remember` CLI.)

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `agent` | Yes | — | Agent name; must be in the team's agent configuration |
| `content` | Yes | — | The text to remember |
| `kind` | No | "note" | What kind of memory: "note", "decision", or "fact" |

**Returns:** Confirmation envelope with the written file path.

**When to use:** Whenever the agent learns something worth keeping. Pair it with `contradict` to check for conflicts first.

---

### ingest_chat

Push a JSONL chat transcript into the knowledge store so future search / prep / recall can see it. Pass the agent's own engagement namespace — cross-engagement calls are rejected.

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `jsonl_content` | Yes | — | The transcript as JSONL text, supplied inline |
| `conversation_id` | Yes | — | Identifier for the conversation |
| `namespace` | Yes | — | The agent's engagement namespace |
| `window_turns` | No | 5 | Conversation window size for chunking |
| `no_extract` | No | false | Skip signal/fact extraction (store only) |

**Returns:** Ingest summary (records written, extraction stats).

---

### facts_about

Look up what kairix knows about an entity from the fact store. Returns the current (non-superseded) entity-attribute-value records with confidence and source provenance.

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `entity` | Yes | — | The entity name to look up |
| `namespace` | No | None | Restrict to one engagement scope; omit to search across all namespaces |
| `top_k` | No | 20 | Maximum number of fact records to return |

**Returns:** Fact records with attribute, value, confidence, and source provenance. Each fact now carries a source link you can open back to where it came from, instead of an internal reference that meant nothing on its own.

---

### entity_suggest

Suggest entities (people, organisations, places) found in a block of text via NER plus a Neo4j cross-reference.

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `text` | Yes | — | Text to scan for entities |

**Returns:** Suggested entities with type and whether each already exists in the graph.

---

### entity_validate

Validate a named entity against Wikidata and optionally write the resolved qid back to Neo4j.

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `name` | Yes | — | The entity name to validate |
| `update` | No | false | When true, write the resolved qid to Neo4j |

**Returns:** Validation result with the matched Wikidata qid (if any).

---

> **Recording answer quality.** The eval-feedback loop (EPIC [#465](https://github.com/three-cubes/kairix/issues/465), shipped 2026-06-18) records per-call answer quality through the **MCP call log**, not a dedicated MCP tool. Inspect it with the `kairix mcp-calls` CLI; there is no `record_quality` tool on the server.

## Help + discovery tools

### usage_guide

Get help on how to use kairix tools. Pass a topic to filter, or leave empty for the full guide.

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `topic` | No | "" | Filter topic: "budget", "entity", "troubleshoot", etc. |

**Returns:** The agent usage guide, optionally filtered to the topic.

---

### capabilities

Programmatic capability catalogue — every kairix capability with its MCP tool name, CLI command, category, and (for capped MCP variants) the agent-safe caps. SRE agents call this to discover the surface instead of guessing.

**No parameters.**

**Returns:** The full kairix capability catalogue. Read-only.

---

### recommend_capabilities

Describe a task and get a ranked list of kairix tools, skills, slash-commands, sub-agents, or workflows that fit — each with a why-it-fits note and a ready-to-call invocation. (Shipped via PR [#569](https://github.com/three-cubes/kairix/pull/569) / [#570](https://github.com/three-cubes/kairix/pull/570).) Gated by the `recommender` feature flag; returns a disabled envelope when the flag is OFF.

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `task` | Yes | — | A description of what you're trying to do |
| `agent` | No | "" | Agent name (logged; v1 does not personalise ranking) |

**Returns:** A ranked list of capabilities, each with a why-it-fits explanation and a ready-to-call invocation. Read-only — no LLM sits between you and your tools.

---

## Setup + diagnostics tools

These are how operators (and agents helping with setup) configure kairix and check that everything is healthy. They don't return retrieval content — they return config proposals and health reports. All are read-only unless noted.

### onboard_check

Run the kairix deployment health probes. Call when search seems degraded, before triaging "I expected more results", or after a config change.

**No parameters.**

**Returns:** `{passed, total, fully_passed, failures[]}` — the same shape as `kairix onboard check --json`.

---

### onboard_scan

Walk a knowledge-store root, find agent-shaped subdirectories, and return a proposed `agents:` config block. Auto-detects harness markers (`CLAUDE.md`, `.claude/`, `AGENTS.md`, `.codex/`) and cross-references workspace directories when present.

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `memory_root` | Yes | — | Path containing per-agent subdirectories (e.g. `04-Agent-Knowledge`) |
| `workspace_root` | No | "" | Optional sibling root for per-agent workspace folders |

**Returns:** `{agents: [...], error: ""}` — one entry per discovered agent with name, harness, surfaces, confidence, file count, and most-recent file date.

**When to use:** First-time setup, or after adding a new agent's directory to your knowledge store.

---

### onboard_agent

Same as `onboard_scan` but scoped to one named agent. Useful when adding a single new agent without re-scanning everything.

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `agent_name` | Yes | — | Agent name (must match the subdirectory name) |
| `memory_root` | Yes | — | Path containing the agent subdirectory |
| `workspace_root` | No | "" | Optional sibling workspace root |
| `harness` | No | "" | Force a specific harness (`claude-code`, `codex`, `generic`); omit to auto-detect |

**Returns:** `{agent: {...} | null, error: ""}` — a single proposed scope, or an error message naming the agent.

---

### doctor_check_all

Validate every configured agent's scope against what's on disk. Reports missing paths, empty surfaces, stale files (older than 30 days), and ambiguous globs.

**No parameters.**

**Returns:** `{agents: [...], overall: "ok"|"warn"|"error", summary_text: "...", error: ""}` — health report per agent. `overall` aggregates: `ok` only when every agent is healthy, `error` when any path is missing.

**When to use:** After editing `kairix.config.yaml`, as a CI step, or when an agent reports "no memory logs found".

---

### doctor_check_agent

Same as `doctor_check_all` but for one named agent.

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `agent_name` | Yes | — | Agent to validate |

**Returns:** `{agent: {...}, error: ""}` — single-agent health report with surface-by-surface detail.

---

### worker_status

Read the kairix-worker state file. Call to verify the embed / maintenance loop is running.

**No parameters.**

**Returns:** The worker's phase, counters, last-run timestamp, and last-error string. Identical to `kairix worker status`.

---

### features_status

List the registered kairix feature flags and their effective values. Use it to self-introspect what's enabled before relying on flag-gated behaviour.

**No parameters.**

**Returns:** The feature-flag status envelope. Identical to `kairix features status --json`.

---

### secrets_verify

Operator-facing credential preflight. Walks every kairix-bound secret (LLM, embed, Neo4j, every connector) and reports which canonical key-vault names resolve, which resolve via a deprecated legacy alias, and which are MISSING. Never returns secret values — only canonical names plus resolution status.

**No parameters.**

**Returns:** The secrets resolution envelope. Identical to `kairix secrets verify --json`.

**When to use:** When you want to know "is auth healthy on this deployment?" without `docker exec` access.

---

### dead_letter_status

Operator-facing dead-letter triage view. Returns per-source counts, failure-class buckets, a MIME breakdown, and the oldest five failures.

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `source_name` | No | None | Restrict the view to one source; omit for all sources |

**Returns:** The dead-letter status envelope. Identical to `kairix dead-letter status --json`.

---

### caches_status

Inspect the running MCP server's caches (query cache, prep summary cache, brief output cache, etc.). Returns the warm server-side state, not the calling process's view.

**No parameters.**

**Returns:** `{caches: [{name, size, hits, misses, evictions, hit_rate_pct}, ...], process_pid, process_uptime_s, latency_ms}` — per-cache stats plus the MCP server's process id and uptime so you can confirm you're looking at the real server.

**When to use:** Diagnosing slow queries, confirming caches are warming as expected, sanity-checking a deployment.

---

### warm

Warm kairix retrieval caches and pay the factory-init costs. The first call constructs the search pipeline and runs a tiny read-only probe; agents and entrypoint scripts call this at session start and retry if cold-start is reported (`ready=False`). Idempotent — later calls are sub-millisecond.

**No parameters.**

**Returns:** A warm envelope with `ready`. When `ready=True`, the readiness gate flips so `/healthz/ready` returns 200.

---

### probe_search

Concurrent-load latency probe — a capped, agent-safe surface. Stays within agent-safe caps on `queries` and `concurrency`; requests above the cap return an operator-only escalation envelope instead.

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `suite` | No | "reflib" | Which suite to probe against |
| `queries` | No | 20 | Number of probe queries (capped) |
| `concurrency` | No | 3 | Concurrent request count (capped) |
| `seed` | No | 0 | Random seed for query selection |

**Returns:** A probe result envelope below the cap; an escalation envelope above it.

**When to use:** To confirm retrieval is healthy before a long task.

---

### maintenance_analyze

Run `ANALYZE` on the kairix SQLite index to refresh planner statistics. Reports the query plan before and after on a representative hot-path query so you can confirm the planner picked up the new stats.

**No parameters.**

**Returns:** The analyze envelope with before/after query plans. Equivalent to `kairix maintenance analyze`.

**When to use:** After large ingests, or when query plans look wrong.

---

## Operator-only escalation tools

These tools represent heavy or mutating operations that an agent must not run unattended. Each one returns an `OperatorOnlyCapability` escalation envelope naming the exact CLI command (or Python API) for the operator to run — so an agent can surface the right next step to its human without performing the action itself.

| Tool | What it escalates | Operator runs |
|------|-------------------|---------------|
| `benchmark_run` | Multi-minute search-quality benchmark | `kairix benchmark run` |
| `soak_run` | Multi-minute load / soak test | `kairix.quality.soak.run_soak` Python API (the `kairix soak run` CLI was retired in v2026.6) |
| `probe_burst` | Load-generating throughput-drop probe | `kairix.quality.probe.run_probe_burst` Python API (the `kairix probe burst` CLI was retired in v2026.6) |
| `probe_config` | Embed-workload tuning probe against the provider endpoint | `kairix probe-config` |
| `embed` | Mutates the vector index (consumes provider quota) | `kairix embed` |
| `embed_rebuild_fts` | Drops + recreates the `documents_fts` table | The exact recovery command in the envelope |
| `store_crawl` | Mutates the Neo4j entity graph | `kairix store crawl` |
| `cc_pair` | topology cc_pair lifecycle (list / create / pause / resume / delete) | `kairix cc-pair` |

`benchmark_run`, `soak_run`, and `probe_burst` accept the same parameters as their CLI equivalents (e.g. `suite`, `repeat`, `total_queries`, `peak_concurrency`); `cc_pair` takes a `verb` (default `"list"`); `embed` takes a `limit`. Because they only return an escalation envelope, the parameters are echoed back for the operator rather than executed.

---

## Quick decision guide

| Situation | Tool to use |
|-----------|------------|
| "Find documents about X" | **search** |
| "Give me the chunks around this hit" | **expand** |
| "Research X in depth" | **research** |
| "Who is X?" / "What is Company Y?" | **entity** |
| "Quick summary of X" | **prep** |
| "Answer this date-dependent question" | **timeline** |
| "Does the store already disagree with this?" | **contradict** |
| "Give me a synthesised briefing" | **brief** |
| "Orient me at session start" | **bootstrap** |
| "Remember this for next time" | **memory_write** |
| "Push this conversation into the store" | **ingest_chat** |
| "What does kairix know about this entity?" | **facts_about** |
| "How do I use these tools?" | **usage_guide** |
| "Which tool fits this task?" | **recommend_capabilities** |
| "What agents does kairix see on disk?" | **onboard_scan** |
| "Is my agent setup healthy?" | **doctor_check_all** |
| "Is auth healthy on this deployment?" | **secrets_verify** |
| "Are the caches actually warming?" | **caches_status** |
| "Run a benchmark / soak / heavy job" | **benchmark_run** (escalates to your operator) |
