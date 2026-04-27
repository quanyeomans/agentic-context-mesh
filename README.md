# Kairix

**Private knowledge retrieval for AI agents and teams.**
Your documents, your servers, your agents — finding the right answer in under a second.

[![Apache 2.0](https://img.shields.io/badge/licence-Apache%202.0-blue)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-passing-brightgreen)]()
[![NDCG@10](https://img.shields.io/badge/NDCG%4010-0.818-orange)]()

---

## The problem

AI agents are smart but they have no memory. Ask your agent about a client meeting from last week and it draws a blank — unless you paste the notes in yourself. Your knowledge is sitting in files, but your agents can't find it.

The usual fixes either send your private documents to someone else's servers, give you search results that miss what you actually need, or cost hundreds of dollars a month for enterprise search tools.

## What kairix does

Kairix searches your documents and gives your AI agents (or you) the right answer in under a second. It runs on your own computer or server — your files never leave your control.

It combines two ways of searching: **keyword matching** (finds exact words, file names, codes) and **meaning-based search** (finds related ideas even when the words are different). Then it layers on a **knowledge graph** that understands people, companies, and how they connect to each other.

Three things make it different:

1. **Private by default.** Your documents stay on your machine. The only outbound call is to generate search embeddings (a mathematical fingerprint of your text) — and even that can run locally.

2. **Measurably good search.** Built-in benchmarking tools prove how well it works on your data. The reference deployment finds a relevant document in the top 5 results for **91% of queries** — tested on 293 real questions, scored by an independent judge.

3. **Runs on a laptop.** No GPU. No expensive cloud infrastructure. A basic server with 4 CPUs and 16GB RAM handles 10,000+ documents with multiple agents searching simultaneously. That's about $25/month on any cloud provider, or free on hardware you already own.

---

## Where kairix fits

Kairix is the **knowledge layer** — it sits between your agents and your documents. Your agents ask questions; kairix finds the answers.

```
Your agents (Claude, OpenClaw, LangGraph, CrewAI, or custom)
    ↓ ask questions via MCP tools
Kairix (searches, ranks, and summarises)
    ↓ reads from
Your documents (notes, markdown, PDFs, exports — whatever you have)
```

It works with any agent platform that supports [MCP](https://modelcontextprotocol.io/) (Model Context Protocol). Your agent asks kairix a question with one tool call; kairix handles the searching, ranking, entity lookup, and budget management behind the scenes.

### Works well with

- **[OpenClaw](https://openclaw.ai)** — register kairix as an MCP server and every OpenClaw agent gets search tools automatically. Runs on the same VM — kairix adds ~200MB RAM alongside your gateway. Agents search before every task instead of guessing from memory.
- **[Claude Code](https://claude.ai/claude-code)** / **Claude Desktop** — add kairix to your MCP config and Claude uses it as a knowledge source during conversations and coding sessions.
- **[LangGraph](https://github.com/langchain-ai/langgraph)** / **[CrewAI](https://github.com/crewAIInc/crewAI)** — kairix's `tool_research` MCP tool does iterative multi-turn search, so your agent graph gets a retrieval node that refines its own queries until it finds a good answer.
- **Any MCP-compatible agent** — stdio or SSE transport, no custom integration code needed.

---

## Token cost: kairix vs stuffing the context window

Most approaches to giving an agent knowledge involve dumping documents into the prompt. This gets expensive fast.

| Method | Tokens per query | Cost per 1,000 queries (Sonnet) | Quality |
|--------|-----------------|-------------------------------|---------|
| **Stuff everything** (paste all docs) | 50,000–200,000 | $150–600 | Poor — LLM drowns in noise |
| **Naive RAG** (top-5 full docs) | 10,000–30,000 | $30–90 | OK — but no ranking, no budget |
| **Kairix search** (budget-managed) | 1,500–5,000 | $4.50–15 | Good — ranked, entity-aware, right-sized |
| **Kairix prep** (quick summary) | 500–1,500 | $1.50–4.50 | Good for quick lookups |

Kairix controls how much context each query returns. A quick fact check gets 1,500 tokens. A research question gets 5,000. Your agents never send the LLM more than it needs — which means lower cost and better answers (less noise for the model to sort through).

**Embedding cost** (one-time per document): indexing 10,000 documents costs about $3 with Azure OpenAI `text-embedding-3-large`. Hourly incremental updates cost fractions of a cent.

---

## How it compares

| | Kairix | Azure AI Search | QMD | Notion AI / Confluence AI | Stuffing the context window |
|---|---|---|---|---|---|
| **Your data stays private** | Yes — nothing leaves your servers | Azure cloud (your tenant) | Yes (local) | Vendor cloud | Sent to LLM provider |
| **Finds the right document** | Keyword + meaning + knowledge graph, fused | Keyword OR meaning (not fused) | Keyword only (BM25) | Keyword only | No search — sends everything |
| **Knows who people are** | Yes — entity graph links people, companies, decisions | No | No | No | No |
| **Answers date questions** | Yes — "what happened last week" just works | Manual filters | No | No | No |
| **Controls what the LLM reads** | Yes — budget per query (saves money) | No budget control | No | Sends full pages | Sends everything |
| **Proves it works** | Benchmarked: 0.80 NDCG@10 on real queries | Not published | Not published | Not published | N/A |
| **Needs a GPU** | No | No | No | N/A (SaaS) | No |
| **Cost** | ~$25/month | $250+/month | Free | $8-10/user/month | LLM token costs |
| **Works with agents (MCP)** | Built-in — 6 MCP tools | Custom integration needed | No | No | Manual prompt building |

---

## Quick start

### Option A: pip install (recommended)

```bash
pip install "kairix[agents,neo4j]"
kairix setup                   # interactive wizard — picks your paths, ports, collections
kairix embed                   # index your documents
kairix search "your question"  # find answers
kairix mcp serve               # start MCP server for agent integration
```

### Option B: Docker Compose

```bash
git clone https://github.com/quanyeomans/kairix && cd kairix
cp .env.example .env        # add your LLM API key
ln -s ~/my-notes ./documents # point to your documents
docker compose up -d         # starts kairix + worker + Neo4j
```

See the [full quick-start guide](docs/quick-start.md) for detailed setup, configuration, and troubleshooting.

**What you need:**
- Python 3.10+ (Option A) or Docker (Option B)
- An LLM API key for embeddings (Azure OpenAI or OpenAI-compatible)
- A folder of documents (markdown, text, or structured notes)

**Ships with:** 6,500+ curated reference library documents — searchable out of the box, no indexing wait.

---

## What it costs to run

| Component | Monthly cost | What you get |
|-----------|-------------|--------------|
| VM (4 vCPU, 16GB) | ~$20 | Runs everything — search, embedding, Neo4j, agents |
| LLM API (embedding) | ~$3-5 | Index 4,000 documents, hourly incremental updates |
| LLM API (search/prep) | ~$2-5 | Depends on query volume |
| **Total** | **~$25-30** | Full private knowledge platform |

No GPU required. No per-seat licensing. One VM serves an entire small team.

Compare: enterprise knowledge platforms start at $500+/month. Cloud RAG services charge $50-200/month per workspace. Kairix runs on infrastructure you already have.

---

## For agent builders

Kairix exposes an MCP server that any MCP-compatible agent can call. One tool, one question — kairix handles the rest.

```bash
# Start the MCP server
pip install "kairix[agents]"
kairix mcp serve
```

**What the agent sees:**

```
mcp-kairix__search("brief for quarterly client meeting")
→ Returns: ranked results, entity context, budget-managed content
```

The system handles automatically:
- **Right-sized responses** — quick lookups get small answers; research questions get thorough ones
- **Date-based questions** — "what happened last week" is rewritten with specific dates before searching
- **People and company context** — if the question is about a known person or company, their knowledge graph summary appears at the top

Other MCP tools available: `entity` (direct person/company lookup), `prep` (quick topic summary), `timeline` (date query inspection).

---

## Measuring quality on your data

Kairix ships with tools to evaluate search quality on your own documents — not just the reference deployment.

```bash
# Build a gold-standard test suite from your data
# (uses multiple search methods + LLM judge to create unbiased relevance scores)
kairix eval build-gold --suite queries.yaml --output gold.yaml

# Test different search configurations against your gold suite
kairix eval hybrid-sweep --suite gold.yaml --output results.csv

# Run the standard benchmark
kairix benchmark run --suite gold.yaml
```

The evaluation methodology uses TREC-style pooling (the same approach used by academic search competitions) with LLM-as-judge relevance scoring. This means the test suite isn't biased toward any particular search configuration — it measures what's relevant to the questions, independently.

**Reference deployment scores (293 real queries, independently judged):**

| Metric | Score | What it means |
|--------|-------|--------------|
| NDCG@10 | 0.803 | Documents are found AND ranked in the right order |
| Hit@5 | 91.1% | 9 out of 10 queries find a relevant document in the top 5 |
| MRR@10 | 0.746 | The first relevant result appears at position 1.3 on average |

---

## How it works

You have documents. Kairix indexes them. When you or your agents ask a question, it finds the best answers.

**Search:** Combines keyword matching (finds exact terms, file names, codes) with meaning-based search (finds related concepts even when the words don't match). The best results from both approaches are merged, with keyword matches ranked first and meaning-based discoveries appended for recall.

**Entity awareness:** If you connect a knowledge graph (Neo4j), kairix understands relationships. A question about a client surfaces their related contacts, recent decisions, and relevant research — not just documents that mention the client's name.

**Date handling:** Questions like "what did we decide last month" are automatically rewritten with specific date ranges before searching. No special syntax needed.

**Token budget:** Each search has a configurable context budget. Quick fact checks get small responses (1,500 tokens). Research questions get thorough ones (5,000 tokens). Agents can override, but the defaults are smart.

**Fusion strategy:** The system defaults to BM25-primary fusion (keyword results first, meaning-based results appended). If your documents are heavy on unstructured prose with little keyword overlap, you can switch to RRF fusion via configuration. Run `kairix eval hybrid-sweep` to find the best strategy for your data.

---

## Capabilities

| What | Status | Plain description |
|---|---|---|
| `kairix embed` | Shipped | Indexes your documents for search (keyword + meaning-based) |
| `kairix search` | Shipped | Finds the best answers to any question |
| `kairix mcp serve` | Shipped | MCP server with 6 tools — search, entity, prep, timeline, research, usage guide |
| `tool_research` (MCP) | Shipped | Iterative research — searches multiple times, refining until it finds a good answer |
| `kairix eval` | Shipped | Measures and improves search quality on your data |
| `kairix vault crawl` | Shipped | Builds a knowledge graph from your document structure |
| `kairix brief` | Shipped | Generates a session briefing for an agent before it starts work |
| `kairix prep` | Shipped | Quick topic summary (cheaper than full search) |
| `kairix benchmark` | Shipped | Runs quality benchmarks against a test suite |
| `kairix entity suggest` | Shipped | Discovers people, companies, and concepts in your documents |
| `kairix classify` | Shipped | Routes new knowledge to the right place in your vault |
| `kairix contradict` | Shipped | Flags conflicting facts before they persist |
| `kairix curator health` | Shipped | Monitors knowledge graph quality |

---

## Roadmap

**Working now:** Hybrid search, knowledge graph, temporal reasoning, session briefings, MCP server (6 tools), iterative Researcher Agent (LangGraph), evaluation tooling, configurable fusion strategies, budget auto-inference.

**Coming next:**
- Connector framework — ingest from SharePoint, CRM, email headers
- Curator agent — proactive knowledge harvesting and gap detection
- Cross-encoder re-ranking evaluation
- Multi-hop query improvement (weakest category at 0.572)

See [ROADMAP.md](ROADMAP.md) for detail.

---

## Install

```bash
# Core (search, embed, eval)
pip install kairix

# With knowledge graph support
pip install "kairix[neo4j]"

# With MCP server for agent integration
pip install "kairix[agents]"

# Everything
pip install "kairix[neo4j,agents,nlp]"
```

**Prerequisites:**
- Python 3.10+
- An LLM API key for embeddings (Azure OpenAI, or OpenAI-compatible)
- A folder of documents to index

**Optional:**
- Neo4j Community Edition — for knowledge graph features
- Azure Key Vault — for production secret management

See [OPERATIONS.md](OPERATIONS.md) for full deployment guide.

---

## Development

```bash
git clone https://github.com/quanyeomans/kairix
cd kairix
pip install -e ".[dev,neo4j,agents]"
pytest tests/                    # 1,634 tests
ruff check kairix/ tests/        # lint
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for architecture, PR process, and versioning.

---

## Research foundations

The retrieval design builds on validated approaches from information retrieval research:

- **Hybrid search** — combining keyword and meaning-based retrieval consistently outperforms either alone ([Thakur et al., BEIR 2021](https://arxiv.org/abs/2104.08663))
- **Entity-aware retrieval** — knowledge graph augmentation improves recall on questions about people, companies, and concepts ([REALM, Guu et al. 2020](https://arxiv.org/abs/2002.08909))
- **Evaluation methodology** — TREC-style pooling with graded relevance ([Voorhees & Harman, TREC](https://trec.nist.gov/)); LLM-as-judge with position-bias mitigation
- **BM25-primary fusion** — preserves keyword ranking precision while gaining semantic recall (validated via 38-configuration parameter sweep)

---

## Data residency

Document content is sent to your configured LLM endpoint for embedding only. No content is stored externally. All indexes, vectors, and knowledge graph data live in SQLite and Neo4j on your own infrastructure.

See [SECURITY.md](SECURITY.md) for the full security posture.

---

## Licence

Apache 2.0 — see [LICENSE](LICENSE).

Built on: [sqlite-vec](https://github.com/asg017/sqlite-vec) (Alex Garcia), [SQLite FTS5](https://www.sqlite.org/fts5.html), [Neo4j Community Edition](https://neo4j.com/).
