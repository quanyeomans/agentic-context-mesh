# What kairix does for you

Two audiences use kairix daily: the **humans** running a small team alongside AI agents, and the **agents** themselves. The product surface is the same for both — one MCP server, one CLI, one knowledge store — but what each audience gets out of it is different. This page covers both views.

If you're brand new and want to install kairix, start at [quick-start.md](../getting-started/quick-start.md). Come back here when you want to know *why* you're installing it.

---

## For humans running a small team

You're operating an agent-augmented team. Your agents are writing notes, making decisions, calling clients, running research. Without something in the middle, every agent dumps content wherever it happens to be working, contradicts itself across sessions, and forgets who your clients are between conversations. You end up curating their output by hand.

Kairix is the middle layer. It does five things that take work off your plate.

### 1. One place to look for everything your team and agents have produced

`kairix search "the question"` searches across every connected source — your notes, your agents' memory logs, your SharePoint, your Slack, your GitHub — and returns the top results within a token budget. You stop hunting across tools. Your agents stop dumping 50,000 tokens of raw documents into a prompt to "make sure they have context".

### 2. Agents put new notes in the right place automatically

When an agent writes something new — a meeting note, a decision, a research output — kairix classifies it by type and routes it to the right location in your knowledge store. You don't have to tell each agent where its notes go. The structure stays consistent because it's enforced by the layer, not by every agent remembering instructions.

### 3. Agents check themselves before adding contradictions

Before saving a new fact or decision, an agent can ask kairix "does this contradict anything?" If your team already decided X in March and the agent is about to write not-X in June, kairix flags it. You catch the contradiction before it gets saved, not after you discover the mess.

### 4. People, companies, and relationships are first-class

Kairix builds a knowledge graph of people, organisations, and relationships from your documents. When an agent asks "who is the contact at <client>?", it gets the contact, related work, recent decisions, open items — not a list of documents that mention the name.

### 5. Your files stay on your machine

The whole stack runs locally (or on your own infrastructure). No vendor cloud, no documents-as-training-data exposure, no monthly per-seat fee that scales with team size. You bring an LLM API key (Azure, OpenAI, Anthropic via litellm, Ollama for local models) and that's the only external dependency.

**Set-up time for a real team:** half a day for the docker-compose install, half a day to run `kairix onboard scan` and commit the per-agent config, then a couple of hours per source connector (SharePoint, Slack, GitHub, etc.) as you bring them on. Most teams are productive end of week one.

---

## For agents using kairix as part of a session

You're an LLM agent. A user has connected you to a kairix MCP server. Read this section once and the patterns become muscle memory.

### Why search through kairix beats searching your context

Your context window is finite. A single document the user wants you to read can be 5,000-15,000 tokens — eat three of those and you've consumed most of a 50K conversation. Kairix returns the top 5 ranked snippets within a 3,000-token budget by default. In a 200K context window, that's 60-ish searches per session instead of 5. You can actually iterate on a question.

### What to call and when

You have nine routine tools. Use this decision table:

| You need to... | Call |
|---|---|
| Answer any factual question about prior work, decisions, or context | `search` |
| Do iterative research that needs multiple search passes | `research` |
| Look up one named person, company, or concept | `entity` |
| Get a quick context summary before a more expensive call | `prep` |
| Find what happened in a date range | `timeline` |
| Get oriented at session start (role, board, recent memory, goals) | `bootstrap` |
| Write a structured session briefing | `brief` |
| Check whether a new claim contradicts existing knowledge | `contradict` |
| Look up help on these tools | `usage_guide` |

Five more tools exist for setup and diagnostics — operators use them, agents rarely need them. They're documented in [mcp-tools.md](mcp-tools.md).

### Cold-start behaviour

The first call after an MCP server boots may return `HTTP 503` with a `Retry-After` header and a body containing `error_code: KAIRIX_COLD_START`. This isn't an error — kairix is warming. Wait the number of seconds in `retry_after_ms`, then call again. The second call returns the real answer. If the second call also returns cold-start, surface to the user that kairix is still warming (typically ~14 seconds total, occasionally longer on first deploy). The full envelope reference is in [cold-start-envelope-reference.md](../operations/runbooks/cold-start-envelope-reference.md).

### Scope: shared vs agent vs shared+agent

Most calls accept a `scope` parameter. The default is `shared+agent` — search both the team knowledge store and the calling agent's private memory. Switch to `shared` when you want to avoid your own prior notes biasing the result. Switch to `agent` when the question is about your own session history specifically.

### Don't paste documents back to the user

If a search result has `path: /data/documents/...`, that path is on the user's machine. Cite it ("see `path/to/file.md`") instead of pasting the whole document. The whole point of search is that the user doesn't need the full document in the conversation.

### When something looks wrong

If you call a tool and the response shape doesn't match what's documented here, the version mismatch is the first thing to check — call `usage_guide` (no topic) and compare the tool list to what you expected. Operators upgrading kairix may have added or removed tools.

---

## Editorial guidelines (for anyone writing docs in this repo)

This is a public-facing repo. Three rules cover most of what we get wrong:

- **Write at grade 8 reading level.** A small-team operator reads this on a Tuesday afternoon between calls. No jargon you wouldn't say out loud. Drop internal naming (envelope, intent classifier, fitness function, dispatcher) in favour of what the user sees ("structured error", "query classifier", "checks", "router" — or just describe the behaviour).
- **Talk about user experience first, internal architecture second.** "Brief returns under a second through warm MCP" is the user-facing fact. "PR 2.8 dropped the `_wants_json_output` dispatcher gate" is implementation history that belongs in a commit message, not a release note.
- **No client names, no internal sprint metadata, no specific future version numbers.** Public artefacts (CHANGELOG, docs, release notes, BDD scenarios) use generic agent names (`agent-alpha`, `your-team`) and time-relative language ("shipping soon", "next release") rather than naming specific clients or pinning unshipped features to specific versions.

The internal sprint/phase/PM work happens in the operator's private knowledge store, not in this repo. This repo stays user-facing. CHANGELOG entries are 15-45 lines max — link to `docs/upgrades/v<version>.md` for the longer explanation.
