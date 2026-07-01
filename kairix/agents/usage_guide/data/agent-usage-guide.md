---
type: reference
scope: shared
tags: [kairix, agent-knowledge, search, retrieval]
---

# Kairix Agent Usage Guide

> **First-time users:** Run `kairix setup` to configure your environment before proceeding. See [docs/getting-started/quick-start.md](../getting-started/quick-start.md) for full installation instructions.
>
> **If you're an LLM agent reading this** and you've been asked to set kairix up on a user's behalf, see [docs/getting-started/agent-driven-setup.md](../getting-started/agent-driven-setup.md) — the declarative recipe optimised for unambiguous machine instructions. Then come back here for retrieval-time usage.

This guide is for AI agents using kairix to search and retrieve knowledge from the shared knowledge store. Read it before your first session, and use it as a reference when queries return unexpected results.

---

## What kairix is

Kairix is the retrieval layer between you and the team's knowledge base. It indexes the document store (Obsidian markdown files), runs hybrid search (BM25 + vector), and returns ranked snippets within a token budget. It understands query intent — so a question about "what happened last week" gets different treatment than "what is the engineering pattern for retries".

You do not need to use basic keyword search. Kairix routes your query to the right retrieval strategy automatically.

---

## The core loops — read this first

Almost everything you do with kairix is one of two loops: the **read loop** and the **write loop**. Learn these before the per-command detail further down.

### Read loop: search → expand

Always start with `search`. It finds the most relevant snippets across the knowledge store. A snippet is only a small window into a larger document, so when a hit looks right but the snippet is too thin to answer with confidence, call `expand` on that hit to pull the text around it — the paragraphs before and after, up to the section it belongs to.

```bash
kairix search "how we decided to handle retries" --agent builder --json
# take the top hit's source_uri + seq from the results, then:
kairix expand "<source_uri>" <seq> --token-budget 2000
```

`search → expand` is the intended pattern for any question you are going to answer or cite. Search gives you the lead; expand gives you the surrounding evidence. Do not answer from a single thin snippet when expand can hand you the full passage cheaply — it reads straight from the index, so there is no re-search cost.

**Wire `source_uri` into every final answer.** Each result — from search, briefings, fact lookups, timelines, research, and contradiction checks — carries a `source_uri` you can show a human or hand back to `expand`. Cite it so the reader can open the original evidence.

### Write loop: remember + ingest_chat

When you learn a durable fact or make a decision worth keeping, write it back so the next session can recall it.

```bash
kairix remember "We chose PostgreSQL for the jobs table because ..." --agent builder
kairix ingest-chat --agent builder --transcript <path-to-transcript.jsonl>
```

- `remember` (MCP: `tool_memory_write`) stores a single fact or decision now, so it can be recalled later.
- `ingest-chat` (MCP: `tool_ingest_chat`) saves a whole chat transcript into the knowledge store for later recall.

You do **not** need to run diagnostics before every write — just write. Only *if* a write fails, run `kairix doctor agent --name <you>` (MCP: `tool_doctor_check_agent`) to see what is misconfigured, fix it, and retry.

### How to work well — a few rules that pay off

- **Cite your source.** Put the `source_uri` behind every claim in a final answer (see the read loop above).
- **`contradict` checks coverage, not truth.** `contradict` (MCP: `tool_contradict`) tells you whether the store already holds something that conflicts with what you are about to write — a provenance-and-coverage check. It is not a truth oracle: a "no conflict" result means the store is quiet on the point, not that your claim is correct. Treat it as a prompt to look closer, not a green light.
- **Deep-read from a briefing.** `kairix brief` ends with a `## Sources` list. When a briefing line matters, `expand` its source to read the full passage before you rely on the summary.
- **Reach for `search` first, not `entity` or `facts_about`.** Entity lookup (`kairix entity`) and stored-fact recall (`kairix facts about`) help once the knowledge store has good coverage of a person or topic, but today they can come back thin. Start with `search → expand`; fall back to `entity` / `facts_about` when you specifically want a curated summary or a previously-saved fact.

---

## How to call kairix

```bash
kairix search "<your query>" --agent <your-agent-name>
```

Examples:
```bash
kairix search "what decisions were made about the Azure connector" --agent builder
kairix search "knowledge management positioning" --agent builder --budget 3000
kairix search "how do I run the embedding pipeline" --agent builder
kairix search "what happened last week" --agent builder
kairix search "tell me about Acme Corp" --agent builder
```

**If kairix is not on PATH** (you get `command not found`):
```bash
/usr/local/bin/kairix search "<query>" --agent <name>
```

---

## Flags that matter

| Flag | Default | When to use |
|---|---|---|
| `--agent <name>` | None | Always — scopes results to your agent's collections + shared |
| `--scope <value>` | `shared+agent` | Override the default scope. See "Scope" section below. |
| `--budget <tokens>` | 5000 | Reduce if context window is tight; 2000–3000 is usually enough |
| `--json` | Off | Machine-readable output — use when parsing results programmatically |

---

## Scope

Every retrieval tool (`search`, `prep`, `timeline`, `contradict`) accepts a `scope` value. It controls which document collections the search reaches.

| Scope | Reaches | When to use |
|---|---|---|
| `shared` | Shared collections only (vault content not tied to any agent) | When the agent's own memory shouldn't influence the answer — e.g. fact-checking a claim against curated knowledge. |
| `agent` | Only the calling agent's own memory | When you specifically want to recall what *this agent* has previously written — e.g. session continuity. |
| `shared+agent` (default) | Shared + the calling agent's memory | Usual case — the agent has access to organisational knowledge plus its own history. |
| `all-agents` | Every agent's memory, no shared | Cross-agent synthesis — "what has the team collectively discovered about X?" Requires `agents:` configured in `kairix.config.yaml`. |
| `everything` | Shared + every agent's memory | Maximum-recall queries; treat as a last resort because it dilutes precision. |

**MCP equivalents:** the same values (as strings) are accepted on the `scope` parameter of `mcp-kairix__search`, `mcp-kairix__prep`, `mcp-kairix__timeline`, and `mcp-kairix__contradict`.

---

## How intent routing works

Kairix classifies your query before running search. The classification changes which retrieval strategy fires:

| Intent | Triggered by | What happens |
|---|---|---|
| **keyword** | Version strings, error codes, file names, proper nouns | BM25 + vector in parallel; exact terms weighted highly |
| **entity** | "tell me about X", "what has Y been working on", person/org names | Entity graph lookup + ranked knowledge store docs |
| **temporal** | "last week", "April 2026", "decisions in March", "what happened recently" | Date-filtered retrieval; handles both absolute dates ("April 2026") and relative phrases ("last week") |
| **procedural** | "how do I", "what are the steps to", runbook queries | Path-weighted re-rank; step-relevant docs ranked above background |
| **multi_hop** | "connection between X and Y", "how does A relate to B" | Query decomposed into sub-queries, results fused; cross-encoder re-ranked if [rerank] extra installed |
| **semantic** | Abstract conceptual questions | Pure vector search with HyDE (hypothetical document embedding); cross-encoder re-ranked if [rerank] extra installed |

**You don't need to worry about this.** It's automatic. But if a query returns poor results, knowing the intent can help you rephrase it.

---

## What good results look like

A healthy search result in JSON format (`--json`) has:
```json
{
  "intent": "entity",
  "results": [...],
  "vec_count": 4,
  "bm25_count": 3,
  "vec_failed": false,
  "total_tokens": 1823
}
```

Key fields to check:
- `vec_failed: false` — vector search is working. If `true`, you're on BM25-only.
- `vec_count > 0` — vectors returned. If 0 with `vec_failed: false`, the query had no semantic matches.
- `results` — list of ranked documents with `path`, `score`, and `snippet`

Every result carries a `source_uri` — a source link you can open back to the original. The same pointer is on results from search, briefings, fact lookups, timelines, research, and contradiction checks, so you can always show a human where an answer came from, or feed it to `expand` to read more around a hit.

---

## What to do when results are poor

### `-32602 Invalid request parameters` on every MCP call (post-v2026.5.3 only)

You're hitting the legacy `/sse` endpoint and the gateway is dropping the idle connection. Update your MCP client config to point at `/mcp` instead — see [MCP-CLIENT-MIGRATION.md](../operations/MCP-CLIENT-MIGRATION.md). The migration is a one-line URL change in your client config; the old `/sse` path stays mounted, so this is a fix for your client, not a kairix change.

### If you see `fetch_failed` from kairix

`fetch_failed` is **transient cold-start**, not "kairix is broken". The kairix container is restarting or finishing its warm-up; the right move is to wait and retry, not to fall back to memory or skip the search.

Two shapes you might see during a cold-start window:

1. **HTTP 503 with `Retry-After: 8`** — kairix is alive but warming. Wait the requested seconds (default 8) and retry the same call once.
2. **MCP tool envelope with `error_code: "KAIRIX_COLD_START"`** — the readiness gate is still closed at the application layer. The envelope carries `retry_after_ms` and an `agent_instruction` field. Honour both: wait `retry_after_ms / 1000` seconds, then retry the same call once.

If the second call also fails, surface "kairix is warming up — try again in a minute" to the user. **Never substitute a memory-based answer for a `KAIRIX_COLD_START` response.** Operators monitor the cold-start log events to detect persistent warm-up failures; a fabricated answer would mask the signal.

Full contract: see [`docs/operations/MCP-DEPLOYMENT.md`](../operations/MCP-DEPLOYMENT.md#cold-start-affordance-contract).

### vec_failed=true (vector search broken)
This means Azure credentials aren't loaded. Every search falls back to BM25-only, which misses semantic matches.

**Do not proceed with a session on BM25-only retrieval.** Flag it and run:
```bash
kairix onboard check
```

This will tell you exactly which credential is missing and how to fix it.

### 0 results
Try rephrasing more specifically, or check if the relevant document store section has been embedded:
```bash
kairix search "the exact title of a document you know exists" --agent builder
```
If known documents don't appear, the document store may need a re-embed.

### Results seem off-topic
The intent classifier may have routed incorrectly. Try rephrasing:
- For entity queries: "tell me about [name]" or "what do we know about [organisation]"
- For temporal queries: include explicit relative time language ("last week", "this month", "recent")
- For procedural queries: start with "how do I" or "what are the steps"

---

## All subcommands

### search — the main tool
```bash
kairix search "<query>" --agent <name> [--budget N] [--json]
```

### expand — pull the text around a search hit
```bash
kairix expand "<source_uri>" <seq> [--token-budget 2000]
```
A search hit points at one part of a document. When the snippet isn't enough, call `expand` with the hit's `source_uri` and its `seq` (both come back on the search result) to pull the parts on either side — the section it belongs to — up to a token budget. It reads the surrounding text straight from the index, so you get the context **without** re-searching or re-reading the whole file. Also available as the `tool_expand` MCP tool. Use it right after a hit when you need what comes before and after to answer with confidence.

### brief — session briefing synthesis
Generates a ~800-token briefing synthesising relevant knowledge store content for the start of a session.
```bash
kairix brief <agent-name>
kairix brief shape --budget 5000
```
Output written to `$KAIRIX_DATA_DIR/briefing/<agent>-latest.md`. Each briefing ends with a `## Sources` list of the sources behind it, so you can open any of them to check the work before you rely on the summary.

### entity — entity graph lookup
```bash
kairix entity lookup "Jordan Blake"
kairix entity lookup "Acme"
```
Returns entity summary, type, vault_path, and related documents.

### curator health — entity graph health check
```bash
kairix curator health
kairix curator health --json
```
Reports: entity counts, synthesis failures (no summary), missing vault_paths.

### vault crawl — populate entity graph from document store
```bash
kairix vault crawl --vault-root /path/to/vault
kairix vault crawl --vault-root /path/to/vault --dry-run
```
Run after adding new organisation or person stubs to the document store.

### classify — route new knowledge to the right document store location
```bash
kairix classify "We decided to use PostgreSQL for the jobs table"
# → type: decision, destination: decisions.md, confidence: 0.95
```

### contradict — check new content against knowledge store
```bash
kairix contradict check "We use PostgreSQL for all persistence" --top-k 5
```
Returns contradicting knowledge store documents with conflict scores. Also exposed as the `tool_contradict` MCP tool (see below).

### contradict (MCP: tool_contradict) — check facts before writing
```bash
kairix contradict check "We use PostgreSQL for all persistence" --top-k 5
```
Also available as the `tool_contradict` MCP tool. Agents can call it before writing new content to verify it does not conflict with existing knowledge. Returns contradicting documents with conflict scores.

### onboard check — deployment diagnostics
```bash
kairix onboard check
kairix onboard check --json
```
Run this if search is behaving unexpectedly. Reports: PATH, wrapper, secrets, document store root, vector search, Neo4j.

### timeline — temporal query tools
```bash
kairix timeline query "decisions last week"
```

### wikilinks — inject entity links
```bash
kairix wikilinks inject --vault /path/to/vault
```

### benchmark — retrieval quality testing
```bash
kairix benchmark run --suite suites/example.yaml
```

---

## Common agent session patterns

### Session start (standard)
```bash
# Pull a briefing for context before the session
kairix brief shape

# Then search for session-specific context
kairix search "current status of [project]" --agent builder
kairix search "outstanding items from last week" --agent builder
```

### Researching an entity
```bash
# Start with entity lookup for curated summary
kairix entity lookup "Acme"

# Follow up with related knowledge store docs
kairix search "Acme engagement history and decisions" --agent builder
```

### Checking a decision or pattern
```bash
kairix search "how we decided to handle [topic]" --agent builder
kairix search "engineering pattern for [approach]" --agent builder
```

### Temporal research
```bash
kairix search "what decisions were made last month" --agent builder
kairix search "recent activity on the Azure connector" --agent builder
# Use explicit relative time language for best results
```

### Multi-hop / cross-entity
```bash
kairix search "connection between Acme and TechCorp on the platform project" --agent builder
```

### Research (MCP: tool_research)
The `tool_research` MCP tool runs iterative multi-turn search, refining queries until it finds a good answer. It always returns a synthesis — if no relevant documents are found, it synthesises what it can from the best available results rather than returning a failure message.

---

## Token budget guidance

| Use case | Recommended budget |
|---|---|
| Session-start context | 5000 (default) |
| Quick fact lookup | 2000 |
| Deep research | 8000–10000 |
| Briefing synthesis context | 5000 |

Set with `--budget N`. The budget caps total tokens returned, not the number of documents. Kairix ranks documents and returns as many as fit.

---

## Troubleshooting quick reference

| Symptom | Likely cause | Fix |
|---|---|---|
| `command not found` | kairix not on PATH | Use `/usr/local/bin/kairix` or run `scripts/install.sh` |
| `vec_failed: true` | Azure credentials not loaded | Run `kairix onboard check`; fix secrets_loaded issue |
| 0 results, no error | Document store not embedded | Run `kairix embed --limit 20` to test |
| Results are all from one section | Scope issue | Check `--agent` flag is correct |
| Entity lookup returns nothing | Entity not in Neo4j | Run `kairix vault crawl --vault-root $KAIRIX_VAULT_ROOT` |
| Temporal query returns non-temporal docs | Time phrase not detected | Use relative ("last N days/weeks", "this month") or absolute ("April 2026", "March 15") date references |
| BM25-only (vec_count=0) with valid creds | usearch vector index not built | Run `kairix embed` to build the index |

---

## Capabilities — which surface to use

Every kairix capability has one Python implementation with one or more bindings (CLI, MCP). This table is the index for agents — search it for "diagnostics", "soak", "health", or any capability you're looking for and it tells you which surface to use.

This table lists **every** kairix capability, so nothing you can call is left undocumented. If a capability is not in this table, it is not a live agent capability.

| capability | when to use | how to invoke | surface |
|---|---|---|---|
| `tool_search` / `kairix search` | retrieve content from the knowledge store | MCP — direct | both |
| `tool_expand` / `kairix expand` | pull the chunks around a search hit (before/after/section) within a token budget | MCP — direct | both |
| `tool_entity` / `kairix entity` | named-entity lookup (person, org, project) | MCP — direct | both |
| `tool_timeline` / `kairix timeline` | trace how a topic or project changed over time, in date order | MCP — direct | both |
| `tool_facts_about` / `kairix facts about` | recall the stored facts about a person, project, or topic | MCP — direct | both |
| `tool_prep` / `kairix prep` | tiered L0/L1 context summary before a meeting or hand-off | MCP — direct | both |
| `tool_research` / `kairix research` | gather and synthesise everything the store knows about a broad question | MCP — direct | both |
| `tool_contradict` / `kairix contradict` | check new content for contradictions (a coverage check, not a truth oracle) | MCP — direct | both |
| `tool_brief` / `kairix brief` | session briefing synthesis (ends with a `## Sources` list) | MCP — direct | both |
| `tool_memory_write` / `kairix remember` | remember a fact or decision now so it can be recalled later | MCP — direct | both |
| `tool_ingest_chat` / `kairix ingest-chat` | save a chat transcript into the knowledge store for later recall | MCP — direct | both |
| `tool_bootstrap` / `kairix bootstrap` | session-start orientation envelope | MCP — direct | both |
| `tool_usage_guide` / `kairix usage-guide` | read this guide (full text or filtered by topic) | MCP — direct | both |
| `tool_capabilities` / `kairix capabilities` | list every kairix capability and which surface to use | MCP — direct | both |
| `tool_entity_suggest` / `kairix entity suggest` | propose entities to add to the graph | MCP — direct | both |
| `tool_entity_validate` / `kairix entity validate` | check an entity record for problems before it lands | MCP — direct | both |
| `tool_onboard_check` / `kairix onboard check` | "is kairix healthy?" — read-only probe envelope | MCP — direct | both |
| `tool_onboard_scan` / `kairix onboard scan` | discover which sources and scopes are available to configure | MCP — direct | both |
| `tool_onboard_agent` / `kairix onboard agent` | propose the scope config for a new agent | MCP — direct | both |
| `tool_doctor_check_all` / `kairix doctor agent --all` | check every agent's scope config for drift | MCP — direct | both |
| `tool_doctor_check_agent` / `kairix doctor agent --name` | check one agent's scope config for drift (run this if a write fails) | MCP — direct | both |
| `tool_worker_status` / `kairix worker status` | "is the worker running?" — state file envelope | MCP — direct | both |
| `tool_features_status` / `kairix features status` | which feature flags are on or off | MCP — direct | both |
| `tool_secrets_verify` / `kairix secrets verify` | "is auth wired up?" — read-only secrets status table | MCP — direct | both |
| `tool_dead_letter_status` / `kairix dead-letter status` | how many items failed to ingest, and why | MCP — direct | both |
| `tool_caches_status` / `kairix caches` | cache hit-rate and size | MCP — direct | both |
| `tool_warm` / `kairix warm` | warm the model + index caches before heavy use | MCP — direct | both |
| `tool_maintenance_analyze` / `kairix maintenance analyze` | read-only store health analysis | MCP — direct | both |
| `tool_probe_search` | capped concurrent search probe (latency check) | MCP — direct, capped | MCP |
| `tool_soak_run` / `kairix benchmark run --mode soak` | repeat-and-assert (memory, log volume, fd, determinism) | MCP returns escalation envelope; operator runs CLI | CLI |
| `tool_benchmark_run` / `kairix benchmark run` | retrieval quality measurement | MCP returns escalation envelope; operator runs CLI | CLI |
| `tool_probe_burst` | burst-load latency probe | MCP returns escalation envelope; operator runs CLI | CLI |
| `tool_probe_config` / `kairix probe-config` | inspect the effective retrieval config | MCP returns escalation envelope; operator runs CLI | CLI |
| `tool_embed` / `kairix embed` | embed documents into the vector index | MCP returns escalation envelope; operator runs CLI | CLI |
| `tool_store_crawl` / `kairix store crawl` | rebuild the Neo4j entity graph | MCP returns escalation envelope; operator runs CLI | CLI |
| `tool_embed_rebuild_fts` / `kairix embed rebuild-fts` | drop + re-create the FTS5 table | MCP returns escalation envelope; operator runs CLI | CLI |
| `tool_cc_pair` / `kairix cc-pair` | reconcile contradiction/consistency pairs | MCP returns escalation envelope; operator runs CLI | CLI |

The operator-only rows return an `OperatorOnlyCapability` envelope via MCP — surface the `operator_command` field to your admin if you need the work done.

---

## Getting help

```bash
kairix onboard check           # full deployment diagnostics
kairix --help                  # subcommand list
kairix search --help           # search-specific flags
```

If the diagnostics pass but results are still poor, run a benchmark to establish a baseline:
```bash
kairix benchmark run --suite suites/example.yaml --agent <name>
```

This guide is installed at `04-Agent-Knowledge/shared/kairix-usage.md` in the knowledge store and is searchable via `kairix search "how do I use kairix"`.
