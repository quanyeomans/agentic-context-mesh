# Kairix — Agentic Context Mesh

Private, on-infrastructure contextual retrieval for human-agent teams. Your knowledge stays on your servers. Your agents and teammates query the same indexed knowledge base.

**NDCG@10 0.587** on a 95-case curated real-world benchmark (strict NDCG@10, graded relevance) · **Hit@5 0.821** · **MRR@10 0.679**.

---

## The problem

Skilled professionals accumulate intelligence over years — client knowledge, methods, relationships, decisions. The problem is that this intelligence is fragile: scattered across files, locked in heads, lost in context resets. When a new engagement starts, everyone re-explains context that was already earned. When a tool changes, the institutional knowledge evaporates.

Most AI memory solutions compound this by sending your knowledge to a third-party LLM service:

1. **Privacy** — your organisation's decisions, relationships, and domain knowledge leave your infrastructure permanently
2. **Retrieval quality** — generic RAG without entity awareness, temporal reasoning, or domain-specific patterns produces mediocre results on knowledge that matters
3. **Team coherence** — when agents and humans draw from different sources, shared context breaks down

Kairix is the alternative: a private, on-infrastructure retrieval layer that both human team members and AI agents query against the same indexed knowledge base. Every query compounds the shared understanding. **Your data never leaves your servers.**

---

## How it works

A skilled professional should be able to walk onto any job already knowing it — the history, the plan, the outstanding items. Kairix is the infrastructure for that: a structured knowledge base that agents can query before every session to arrive ready to work.

The design mirrors how experienced professionals think about knowledge:

- **The site** — accumulated knowledge from every engagement: clients, contacts, research, project history, structured so any agent can walk in and immediately understand where things stand
- **Contextual briefing** — before each session, agents pull a synthesised brief: relevant entities, recent activity, outstanding items, content ranked by relevance to the current task
- **Entity traversal** — queries expand across relationships: a question about a client surfaces relevant research, associated contacts, and recent decisions — not just keyword matches

---

## How it differs from alternatives

| | Kairix | Notion AI / Confluence AI | Mem.ai / Rewind | Raw LLM context |
|---|---|---|---|---|
| **Data residency** | Your infrastructure | Vendor cloud | Vendor cloud | API provider |
| **Search approach** | Hybrid BM25 + vector + entity | Full-text only | Vector only | None — full dump |
| **Entity awareness** | Graph with alias resolution | No | No | No |
| **Token efficiency** | Budget-managed retrieval | Unranked export | Unranked export | Unbounded |
| **Temporal reasoning** | Date-aware chunking + routing | No | Limited | No |
| **NDCG@10** | 0.5686 curated real-world | Not published | Not published | N/A |

**On token efficiency:** commercial alternatives typically export full page content and rely on the LLM to filter relevance — you pay for every token regardless of utility. Kairix runs ranked retrieval with a configurable token budget (`--budget`), returning only the highest-relevance chunks within that budget. L0/L1 tiered loading (summary-first, full text on demand) further reduces context consumption.

---

## Research foundations

The retrieval design draws on several well-validated approaches:

- **Hybrid BM25 + dense retrieval** — consistently outperforms either alone on heterogeneous corpora ([Thakur et al., BEIR 2021](https://arxiv.org/abs/2104.08663); Lin et al., sparse-dense fusion). RRF (Reciprocal Rank Fusion) is used for score combination.
- **Entity-aware retrieval** — knowledge graph augmentation significantly improves recall on named-entity queries ([REALM, Guu et al. 2020](https://arxiv.org/abs/2002.08909))
- **Tiered memory** — L0/L1 loading mirrors the sensory → short-term → long-term memory taxonomy from Lilian Weng's [LLM-Powered Autonomous Agents](https://lilianweng.github.io/posts/2023-06-23-agent/) and cognitive science analogues of working memory capacity
- **Temporal routing** — date-aware chunking with explicit timeline indexes improves recall on time-referenced queries, a gap identified in standard RAG evaluations

---

## Architecture

```
kairix search "query" --agent <name>
       │
       ├─ BM25 search (QMD FTS)        ─┐
       ├─ Vector search (sqlite-vec)    ─┤ concurrent
       │                                │
       └─ RRF fusion ◄──────────────────┘
              │
              ├─ Entity boost (Neo4j / entities.db)
              ├─ Token budget cap
              └─ SearchResult → agent context
```

```
kairix brief <agent>
       │
       ├─ Memory logs (last 7 days)
       ├─ Entity stubs (entity collection)
       ├─ Rules + decisions (agent knowledge)
       ├─ Hybrid search (top queries)
       └─ GPT-4o-mini synthesis → briefing.md

kairix classify "<content>"
       │
       ├─ Rule-based classifier (≥90% coverage)
       └─ GPT-4o-mini fallback → vault destination

kairix vault crawl --vault-root /path/to/vault
       │
       ├─ Scans PARA structure → OrganisationNode, PersonNode, OutcomeNode
       ├─ Extracts WORKS_AT edges from frontmatter
       ├─ Extracts MENTIONS edges from [[wikilinks]]
       └─ Upserts into Neo4j (idempotent)
```

All search and entity data is stored in SQLite — no separate vector database. Neo4j Community Edition is used for the entity graph (optional; degrades gracefully when unavailable).

---

## Capabilities

| Module | Status | What it delivers |
|---|---|---|
| `kairix embed` | ✅ Shipped | Azure OpenAI `text-embedding-3-large` → sqlite-vec (1536-dim) |
| `kairix search` | ✅ Shipped | Hybrid BM25 + vector via RRF, token budget management |
| `kairix entity` | ✅ Shipped | Entity graph, alias resolution, entity boost, multi-hop query planning |
| `kairix temporal` | ✅ Shipped | Temporal query rewriting + date-filtered retrieval (TMP-2); `chunk_date` extraction at embed time (TMP-1/5b) |
| `kairix summarise` | ✅ Shipped | L0/L1 tiered context loading |
| `kairix wikilinks` | ✅ Shipped | Wikilink injection + entity resolver |
| `kairix brief` | ✅ Shipped | Session briefing synthesis via GPT-4o-mini |
| `kairix classify` | ✅ Shipped | Auto-classification of memory writes to vault destinations |
| `kairix benchmark` | ✅ Shipped | YAML-driven benchmark runner, NDCG@10/Hit@5/MRR@10 scoring |
| `kairix contradict` | ✅ Shipped | Contradiction detection on new knowledge writes |
| `kairix vault` | ✅ Shipped | Vault crawler → Neo4j entity graph; vault health check |
| `kairix mcp` | ✅ Shipped | MCP server exposing search/entity/prep/timeline to any MCP-compatible agent |
| `kairix curator` | ✅ Shipped | Entity health monitoring and enrichment (CA-1) |

See [ROADMAP.md](ROADMAP.md) for priorities and [ENGINEERING.md](ENGINEERING.md) for design detail.

---

## Benchmark Results

**Suite:** 95 curated queries across 6 categories (entity, keyword, multi_hop, procedural, semantic, temporal), scored with strict NDCG@10 using graded gold relevance. Evaluated on a real-world personal knowledge base of ~2,800 documents (11,316 vectors at 1536-dim).

### Current results (R17 — 2026-04-14)

| Category | NDCG@10 | Notes |
|---|---|---|
| entity | 0.714 | Neo4j entity graph + alias resolution |
| keyword | 0.616 | Full hybrid BM25 + vector |
| procedural | 0.609 | Path boost for how-to/runbook queries |
| temporal | 0.540 | Date-filtered retrieval |
| multi_hop | 0.526 | QueryPlanner, entity-aware sub-query decomposition |
| semantic | 0.501 | Vector search; cross-encoder re-ranking on roadmap |
| **Overall** | **0.587** | **Hit@5 0.821, MRR@10 0.679** |

Production RAG systems on heterogeneous personal knowledge typically score 0.55–0.70 on strict curated suites.

### Score trajectory

| Run | NDCG@10 | Cases | Notes |
|---|---|---|---|
| BM25 baseline | 0.389 | 43 | Pre-vector baseline; synthetic suite |
| R1 post-refactor | 0.7756 | 263 | Full gold rebuild |
| R9 | 0.569 | 95 | Pre-hybrid-fix |
| R13 | 0.603 | 95 | Keyword hybrid fix (+0.110 keyword) |
| **R17** | **0.587** | **95** | Post vault-evolution; Sprint 7 Neo4j |

---

## Prerequisites

- **Python 3.10+**
- **QMD** — installed and indexed (`qmd index` must have run to create `~/.cache/qmd/index.sqlite`)
- **sqlite-vec** extension (`.so`/`.dylib` on `SQLITE_VEC_PATH`, or auto-discovered by QMD)
- **Azure OpenAI** resource with:
  - `text-embedding-3-large` deployment (1536-dim)
  - `gpt-4o-mini` deployment (briefing, classify, benchmark judging)
- **Azure Key Vault** (recommended) — or export secrets as environment variables directly
- **Neo4j Community Edition** (optional) — for entity graph; kairix degrades gracefully when unavailable

See [OPERATIONS.md](OPERATIONS.md) for full infrastructure setup, cron configuration, and first-run sequence.

## Install

```bash
git clone https://github.com/quanyeomans/agentic-context-mesh /opt/kairix
cd /opt/kairix
python3 -m venv .venv
.venv/bin/pip install -e .

# Optional: MCP server support
.venv/bin/pip install -e '.[agents]'
```

Copy and fill in your environment configuration:

```bash
cp env.example service.env
# edit service.env with your Azure endpoint, key vault name, vault paths
source service.env
```

Then follow the [OPERATIONS.md first-run sequence](OPERATIONS.md#first-run-sequence).

---

## Usage

### Embed

```bash
kairix embed                  # incremental — pending docs only
kairix embed --force          # full re-embed
kairix embed --limit 50       # test with first 50 chunks
kairix embed --changed        # re-embed recently modified files
```

### Search

```bash
kairix search "what are our engineering patterns" --agent builder
kairix search "decisions made last week" --agent shape --budget 3000
```

### Entity graph

```bash
kairix entity list
kairix entity lookup "alice chen"
kairix entity write --name "Alice Chen" --type person
kairix entity extract --collection <your-entities-collection>
```

### Vault crawler (Neo4j)

```bash
kairix vault crawl --vault-root /path/to/vault        # populate Neo4j from PARA structure
kairix vault crawl --vault-root /path/to/vault --dry-run  # preview without writing
kairix vault health                                    # entity graph health check
kairix vault health --json                             # machine-readable output
```

### Session briefing

```bash
kairix brief builder           # synthesise briefing for builder agent
kairix brief shape --budget 5000
```

### Auto-classification

```bash
kairix classify "We decided to use PostgreSQL for the jobs table"
# → type: decision, destination: <vault-root>/agent-knowledge/builder/decisions.md, confidence: 0.95

kairix classify "Use monkeypatching for Azure API calls in unit tests"
# → type: pattern, destination: <vault-root>/agent-knowledge/builder/patterns.md, confidence: 0.92
```

### Temporal

```bash
kairix temporal index          # index date-tagged chunks
kairix temporal query "decisions last week"
```

### Wikilinks

```bash
kairix wikilinks inject --vault /path/to/vault
kairix wikilinks audit
```

### Contradiction detection

```bash
kairix contradict check "We use PostgreSQL for all persistence" --top-k 5
kairix contradict check "$(cat new-decision.md)" --threshold 0.7 --format json
```

### Curator health

```bash
kairix curator health          # entity graph health (Neo4j)
kairix curator health --json
```

### MCP server

```bash
# Requires: pip install 'kairix[agents]'
kairix mcp serve                            # stdio transport (Claude Desktop)
kairix mcp serve --transport sse --port 8080  # SSE transport (HTTP)
```

### Benchmark

```bash
kairix benchmark run --suite suites/example.yaml --system hybrid --agent shape
kairix benchmark compare benchmark-results/run-2026-04-07.json
```

---

## QMD Schema Compatibility

Tested against qmd@1.1.2. The embed module validates schema before writing. See [QMD_COMPAT.md](QMD_COMPAT.md) for the exact schema and known constraints.

**Key sqlite-vec constraints (documented with tests):**
- `INSERT OR REPLACE` not supported on vec0 virtual tables — use staging table pattern
- MATCH syntax requires a CTE subquery; direct `FROM vectors_vec JOIN` fails
- Extension must be loaded before any vec0 operation

---

## Development

```bash
.venv/bin/pytest tests/           # unit + integration suite
.venv/bin/pytest tests/embed/     # embed module only
.venv/bin/ruff check kairix/ tests/
```

Coverage: 82% (unit + integration). See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, architecture, and PR process.

---

## Prior Art & Acknowledgements

See [PRIOR_ART.md](PRIOR_ART.md). Key inspirations: [QMD](https://github.com/tobi/qmd) (tobi), [sqlite-vec](https://github.com/asg017/sqlite-vec) (Alex Garcia), OpenViking L0/L1/L2 tiered loading concept, Lilian Weng's [LLM Powered Autonomous Agents](https://lilianweng.github.io/posts/2023-06-23-agent/) memory taxonomy, BEIR hybrid retrieval benchmarks.

---

## Data Residency

Vault content is sent to Azure OpenAI for embedding and synthesis only. No data is stored externally. All vectors and entity data live in SQLite and Neo4j on your own infrastructure. See [EVALUATION.md](EVALUATION.md) and [SECURITY.md](SECURITY.md) for detail.

---

## Licence

MIT — see [LICENSE](LICENSE).
