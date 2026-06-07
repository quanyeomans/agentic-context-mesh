# Kairix MCP Tools

Kairix exposes a set of tools via MCP (Model Context Protocol). Any MCP-compatible agent or IDE can use these to search, research, look up information, and inspect the running server's setup.

The retrieval and synthesis tools (search / research / entity / prep / timeline / usage_guide) are what agents use to find answers. The setup-and-diagnostics tools (onboard_scan / onboard_agent / doctor_check_all / doctor_check_agent / caches_status) are operator-facing — useful when you're configuring a new agent or troubleshooting a slow query.

## Tools

### search

Find answers in your knowledge base. Just pass your question — the system handles date-based queries, budget sizing, and entity detection automatically.

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `query` | Yes | — | Your question |
| `agent` | No | None | Agent name for collection scoping |
| `scope` | No | "shared+agent" | Which collections to search: "shared", "agent", or "shared+agent" |
| `budget` | No | auto | Token budget — automatically sized based on question type (1500 for lookups, 3000 standard, 5000 for research) |

**Returns:** Ranked results with file paths, relevance scores, content snippets, and token counts.

**When to use:** Most questions. This is the default tool for finding information.

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
| `tier` | No | "l0" | "l0" for 2-3 sentences, "l1" for a structured overview |

**Returns:** A brief summary with token count.

**When to use:** Quick context checks, deciding whether to do a full search, getting a baseline understanding of a topic.

---

### timeline

Check how a date-related question will be interpreted. For debugging only — you don't need to call this before searching; date handling is automatic.

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `query` | Yes | — | A question with date references |
| `anchor_date` | No | today | ISO date to anchor relative references |

**Returns:** The rewritten query with explicit dates, detected time window.

---

### usage_guide

Get help on how to use kairix tools. Pass a topic to filter, or leave empty for the full guide.

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `topic` | No | "" | Filter topic: "budget", "entity", "troubleshoot", etc. |

---

## Setup + diagnostics tools

These are how operators (and agents helping with setup) configure kairix and check that everything is healthy. They don't return retrieval content — they return config proposals and health reports.

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

### caches_status

Inspect the running MCP server's caches (query cache, prep summary cache, brief output cache, etc.). Returns the warm server-side state, not the calling process's view.

**No parameters.**

**Returns:** `{caches: [{name, size, hits, misses, evictions, hit_rate_pct}, ...], process_pid, process_uptime_s, latency_ms}` — per-cache stats plus the MCP server's process id and uptime so you can confirm you're looking at the real server.

**When to use:** Diagnosing slow queries, confirming caches are warming as expected, sanity-checking a deployment.

---

## Quick decision guide

| Situation | Tool to use |
|-----------|------------|
| "Find documents about X" | **search** |
| "Research X in depth" | **research** |
| "Who is X?" / "What is Company Y?" | **entity** |
| "Quick summary of X" | **prep** |
| "Why did search interpret my date wrong?" | **timeline** |
| "How do I use these tools?" | **usage_guide** |
| "What agents does kairix see on disk?" | **onboard_scan** |
| "Is my agent setup healthy?" | **doctor_check_all** |
| "Are the caches actually warming?" | **caches_status** |
