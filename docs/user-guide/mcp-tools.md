# Kairix MCP Tools

Kairix exposes 6 tools via MCP (Model Context Protocol). Any MCP-compatible agent or IDE can use these to search, research, and look up information in your knowledge base.

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

## Quick decision guide

| Situation | Tool to use |
|-----------|------------|
| "Find documents about X" | **search** |
| "Research X in depth" | **research** |
| "Who is X?" / "What is Company Y?" | **entity** |
| "Quick summary of X" | **prep** |
| "Why did search interpret my date wrong?" | **timeline** |
| "How do I use these tools?" | **usage_guide** |
