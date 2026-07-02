# How To: Run Kairix Search (Quick Reference)

**Purpose:** Reference for the main kairix query commands — search, brief, timeline, entity — with examples and output interpretation.

---

## kairix search

The primary retrieval command. Runs hybrid BM25 + vector search, enriched with entity graph context.

```bash
# Basic search
kairix search "query text"

# Output format:
# Results: N returned (BM25=A, vec=B) | top score: X.XX | elapsed: Xs
# 1. [0.92] 01-Projects/some-project/BRIEF.md
#    "...relevant excerpt from the document..."
# 2. [0.88] platform/PLATFORM-STANDARDS.md
#    "...excerpt..."

# With category filter
kairix search "embedding lag" --category=ops
kairix search "kairix architecture" --category=architecture
kairix search "delegation tracker" --category=projects

# With top-N result count (default: 8)
kairix search "query" --top=5
kairix search "query" --top=20

# Debug mode (shows ranking internals)
kairix search "query" --debug
# Shows: BM25 score, vec score, RRF combined score, entity boost

# With verbose output
kairix search "query" --verbose
```

**Healthy output indicators:**
- `BM25=N, vec=M` where M > 0
- Elapsed < 500ms (MCP fast path)
- Top result score > 0.7 for specific queries

**Broken indicators:**
- `vec=0, vec_failed=True` → runbook-vector-search-failure.md
- No results for recently added content → runbook-embedding-lag.md
- Elapsed > 5s → check system load

---

## kairix expand

Pulls the chunks around a search hit — the part before, the part after, and the section it belongs to — up to a token budget. Use it after a search hit when the excerpt isn't enough and you need the surrounding context, instead of re-reading the whole document.

```bash
# Pull the neighbouring chunks around a hit, by its source link + position
kairix expand "<source_uri>" <seq>

# Example: expand around chunk 4 of a document the search returned
# (use the source_uri exactly as the search hit reports it — for a plain
#  markdown note that's the document path; for connector content it's the
#  connector URI, e.g. sharepoint://...)
kairix expand "01-Projects/some-project/BRIEF.md" 4

# Widen or tighten how much surrounding text to pull (tokens; default 2000)
kairix expand "01-Projects/some-project/BRIEF.md" 4 --token-budget 4000

# Output: the matched chunk plus its neighbours, in reading order, each with
# its own source link and token count. The matched chunk is always included.
```

**When to use:** right after a `kairix search` hit whose excerpt is too thin to act on. Pass the `source_uri` and `seq` from the search result — no re-search, no whole-file re-read.

---

## kairix brief

Generates a synthesised briefing for a specific agent. First call after TTL expiry takes 2-10s (LLM synthesis); subsequent calls < 500ms (cached).

```bash
# Generate brief for an agent (use your own registered agent names)
kairix brief agent-alpha
kairix brief agent-beta

# Force fresh brief (bypass 4h cache)
kairix brief agent-alpha --refresh

# Brief with specific focus
kairix brief agent-alpha --focus="active projects"

# Output: structured briefing with:
# - Active projects summary
# - Recent decisions
# - Open items requiring attention
# - Relevant context from the document store
```

Brief cache TTL is 4 hours. If expired, `kairix curator health` will flag `brief_cache` yellow — this is expected, not an error. The brief regenerates on demand.

---

## kairix timeline

Retrieves time-ordered events from the document store, filtered by entity or topic.

```bash
# Timeline for an entity
kairix timeline "your-team"
kairix timeline "agent-alpha"
kairix timeline "Kairix"

# Timeline filtered by date range
kairix timeline "kairix" --from=2026-03-01 --to=2026-04-16

# Timeline with topic filter
kairix timeline "agent platform" --top=20

# Output: chronological list of events/decisions with source links
```

---

## kairix entity

Queries the Neo4j entity graph for a named entity and its relationships.

```bash
# Query a named entity
kairix entity "agent-alpha"
kairix entity "your-team"
kairix entity "Kairix"

# Output:
# Entity: agent-alpha
# Type: Person
# Related to: [your-team (founder), ...]
# Mentioned in: [N document-store files]
# Last seen: YYYY-MM-DD

# If entity not found: check the document store has wikilinks → kairix-entity-audit.md (junk/stale entities) or how-to-rebuild-entity-graph.md (rebuild the graph)
```

---

## kairix curator health

Checks the health of all curator subsystems.

```bash
kairix curator health
# Output: green/yellow/red per subsystem:
# memory_size:        green (agent-alpha: 4.2KB, agent-beta: 3.1KB, ...)
# session_log:        green
# brief_cache:        yellow (expired — expected after 4h)
# embedding_freshness: green (last embed: 12m ago)

kairix curator health --verbose
# Shows per-agent memory sizes + session log ages
```

---

## kairix onboard check

Full integration test suite. Run after any config change or service restart. It runs the full deployment check set (~19 probes today, including `agent_memory_writable`, plus any optional-subsystem probes your deployment enables). `fully_passed: true` (exit 0) is the pass signal — gate on that, not a fixed passed/total count, since `total` grows with the subsystems + flags you have on. Add `--json` for the machine-readable envelope.

```bash
kairix onboard check

# Sample of what it tests (dependency order):
# ✓ Secrets loaded (embedding API key, embedding endpoint, neo4j-password)
# ✓ Vector search (embedding API reachable)
# ✓ BM25 search (index accessible)
# ✓ Entity graph (Neo4j reachable)
# ✓ Agent memory writable (ok, or ok(fallback) on a read-only-root deploy)
```

---

## Common Query Patterns

```bash
# What's in a specific project?
kairix search "MCP first architecture" --category=projects

# Find a decision
kairix search "why we chose neo4j"

# Find a runbook
kairix search "fix embedding lag"

# Find recent changes
kairix timeline "kairix" --from=2026-04-01

# Who is involved in something?
kairix entity "Kairix"

# What does an agent know about X?
kairix brief agent-alpha --focus="kairix platform"
```

---

## Related

- how-to-debug-search-ranking.md — tune RRF weights and category scores
- runbook-embedding-lag.md — if new content not appearing in results
- runbook-vector-search-failure.md — if vec=0
- kairix-entity-audit.md — if entity queries return junk or outdated data
- how-to-rebuild-entity-graph.md — rebuild the Neo4j entity graph from the document store
