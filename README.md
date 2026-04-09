# Agentic Context Mesh

Private, on-infrastructure contextual retrieval for human-agent teams. Your knowledge stays on your servers. Your agents and teammates query the same indexed knowledge base.

**NDCG@10 0.58** on an 83-case curated real-world benchmark (strict NDCG@10, graded relevance) · **Hit@5 0.86** — a relevant document appears in the top 5 for 86% of queries. Procedural query boost raised how-to/runbook NDCG from 0.39 to 0.57.

---

## The problem

Skilled professionals accumulate intelligence over years — client knowledge, methods, relationships, decisions. The problem is that this intelligence is fragile: scattered across files, locked in heads, lost in context resets. When a new engagement starts, everyone re-explains context that was already earned. When a tool changes, the institutional knowledge evaporates.

Most AI memory solutions compound this by sending your knowledge to a third-party LLM service:

1. **Privacy** — your organisation's decisions, relationships, and domain knowledge leave your infrastructure permanently
2. **Retrieval quality** — generic RAG without entity awareness, temporal reasoning, or domain-specific patterns produces mediocre results on knowledge that matters
3. **Team coherence** — when agents and humans draw from different sources, shared context breaks down

Agentic Context Mesh is the alternative: a private, on-infrastructure retrieval layer that both human team members and AI agents query against the same indexed knowledge base. Every query compounds the shared understanding. **Your data never leaves your servers.**

---

## How it works

A skilled professional should be able to walk onto any job already knowing it — the history, the plan, the outstanding items. Agentic Context Mesh is the infrastructure for that: a structured knowledge base that agents can query before every session to arrive ready to work.

The design mirrors how experienced professionals think about knowledge:

- **The site** — accumulated knowledge from every engagement: clients, contacts, research, project history, structured so any agent can walk in and immediately understand where things stand
- **Contextual briefing** — before each session, agents pull a synthesised brief: relevant entities, recent activity, outstanding items, content ranked by relevance to the current task
- **Entity traversal** — queries expand across relationships: a question about a client surfaces relevant research, associated contacts, and recent decisions — not just keyword matches

---

## How it differs from alternatives

| | Agentic Context Mesh | Notion AI / Confluence AI | Mem.ai / Rewind | Raw LLM context |
|---|---|---|---|---|
| **Data residency** | Your infrastructure | Vendor cloud | Vendor cloud | API provider |
| **Search approach** | Hybrid BM25 + vector + entity | Full-text only | Vector only | None — full dump |
| **Entity awareness** | Graph with alias resolution | No | No | No |
| **Token efficiency** | Budget-managed retrieval | Unranked export | Unranked export | Unbounded |
| **Temporal reasoning** | Date-aware chunking + routing | No | Limited | No |
| **NDCG@10** | 0.58 curated / 0.94 session | Not published | Not published | N/A |

**On token efficiency:** commercial alternatives typically export full page content and rely on the LLM to filter relevance — you pay for every token regardless of utility. Agentic Context Mesh runs ranked retrieval with a configurable token budget (`--budget`), returning only the highest-relevance chunks within that budget. L0/L1 tiered loading (summary-first, full text on demand) further reduces context consumption.

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
mnemosyne search "query" --agent <name>
       │
       ├─ BM25 search (QMD FTS)        ─┐
       ├─ Vector search (sqlite-vec)    ─┤ concurrent
       │                                │
       └─ RRF fusion ◄──────────────────┘
              │
              ├─ Entity boost (entities.db)
              ├─ Token budget cap
              └─ SearchResult → agent context
```

```
mnemosyne brief <agent>
       │
       ├─ Memory logs (last 7 days)
       ├─ Entity stubs (entity collection)
       ├─ Rules + decisions (agent knowledge)
       ├─ Hybrid search (top queries)
       └─ GPT-4o-mini synthesis → briefing.md

mnemosyne classify "<content>"
       │
       ├─ Rule-based classifier (≥90% coverage)
       └─ GPT-4o-mini fallback → vault destination
```

All search and entity data is stored in SQLite — no separate vector database, no external services beyond Azure OpenAI for embedding and synthesis calls.

---

## Capabilities

| Module | Status | What it delivers |
|---|---|---|
| `mnemosyne embed` | ✅ Shipped | Azure OpenAI `text-embedding-3-large` → sqlite-vec (1536-dim) |
| `mnemosyne search` | ✅ Shipped | Hybrid BM25 + vector via RRF, token budget management |
| `mnemosyne entity` | ✅ Shipped | Entity graph, alias resolution, entity boost, multi-hop query planning |
| `mnemosyne temporal` | ✅ Shipped | Temporal query rewriting + date-aware chunking |
| `mnemosyne summarise` | ✅ Shipped | L0/L1 tiered context loading |
| `mnemosyne wikilinks` | ✅ Shipped | Wikilink injection + entity resolver |
| `mnemosyne brief` | ✅ Shipped | Session briefing synthesis via GPT-4o-mini |
| `mnemosyne classify` | ✅ Shipped | Auto-classification of memory writes to vault destinations |
| `mnemosyne benchmark` | ✅ Shipped | YAML-driven benchmark runner, NDCG@10 scoring, phase gates |
| `mnemosyne contradict` | 🔲 Planned | Contradiction detection on new knowledge writes |

See [ROADMAP.md](ROADMAP.md) for priorities and [ENGINEERING.md](ENGINEERING.md) for design detail.

---

## Benchmark Results

**Suite:** 263 cases across 7 query categories, scored with NDCG@10 using LLM-as-judge (GPT-4o-mini) relevance grading. Evaluated on a real-world personal knowledge base of ~2,800 documents.

### Current results (R1)

| Category | NDCG@10 | Cases | Notes |
|---|---|---|---|
| entity | 0.823 | 47 | Entity graph + alias resolution working well |
| temporal | 0.810 | 39 | Date-aware chunking effective |
| conceptual | 0.804 | 47 | Vector search carrying semantic load |
| keyword | 0.800 | 32 | BM25 baseline solid |
| recall | 0.788 | 49 | Known-document retrieval reliable |
| multi_hop | 0.728 | 33 | QueryPlanner functional |
| procedural | 0.554 | 16 | ✅ Phase 8-A path boost shipped |
| **Overall** | **0.7756** | **263** | |

Production RAG systems on heterogeneous personal knowledge typically score 0.60–0.75.

### Score trajectory

| Run | NDCG@10 | Cases | Notes |
|---|---|---|---|
| BM25 baseline | 0.389 | 43 | Pre-vector baseline; synthetic suite |
| Hybrid Phase 4 | 0.6658 | 43 | First hybrid; synthetic suite |
| Phase 5 real-world | 0.3203 | 134 | First real-world suite; instrument issues |
| Phase 7-A recalibrated | 0.7690 | 252 | After 768→1536 dim correction |
| Phase 7-B 1536-dim | 0.7545 | 252 | Confirmed 1536-dim; keyword +0.114 |
| O-1 entity-graph-planner | 0.7541 | 245 | multi_hop +0.035 from QueryPlanner |
| R1 post-refactor | 0.7756 | 263 | Full gold rebuild |
| **Phase 8-A procedural boost** | **0.554 procedural** | — | Path-weighted re-rank; target ≥ 0.55 met |

---

## Prerequisites

- **Python 3.10+**
- **QMD** — installed and indexed (`qmd index` must have run to create `~/.cache/qmd/index.sqlite`)
- **sqlite-vec** extension (`.so`/`.dylib` on `SQLITE_VEC_PATH`, or auto-discovered by QMD)
- **Azure OpenAI** resource with:
  - `text-embedding-3-large` deployment (1536-dim)
  - `gpt-4o-mini` deployment (briefing, classify, benchmark judging)
- **Azure Key Vault** (recommended) — or export secrets as environment variables directly

See [OPERATIONS.md](OPERATIONS.md) for full infrastructure setup, cron configuration, and first-run sequence.

## Install

```bash
git clone https://github.com/quanyeomans/agentic-context-mesh /opt/mnemosyne
cd /opt/mnemosyne
python3 -m venv .venv
.venv/bin/pip install -e .
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
mnemosyne embed                  # incremental — pending docs only
mnemosyne embed --force          # full re-embed
mnemosyne embed --limit 50       # test with first 50 chunks
mnemosyne embed --changed        # re-embed recently modified files
```

### Search

```bash
mnemosyne search "what are our engineering patterns" --agent builder
mnemosyne search "decisions made last week" --agent shape --budget 3000
```

### Entity graph

```bash
mnemosyne entity list
mnemosyne entity lookup "alice chen"
mnemosyne entity write --name "Alice Chen" --type person
mnemosyne entity extract --collection <your-entities-collection>
```

### Session briefing

```bash
mnemosyne brief builder           # synthesise briefing for builder agent
mnemosyne brief shape --budget 5000
```

### Auto-classification

```bash
mnemosyne classify "We decided to use PostgreSQL for the jobs table"
# → type: decision, destination: <vault-root>/agent-knowledge/builder/decisions.md, confidence: 0.95

mnemosyne classify "Use monkeypatching for Azure API calls in unit tests"
# → type: pattern, destination: <vault-root>/agent-knowledge/builder/patterns.md, confidence: 0.92
```

### Temporal

```bash
mnemosyne temporal index          # index date-tagged chunks
mnemosyne temporal query "decisions last week"
```

### Wikilinks

```bash
mnemosyne wikilinks inject --vault /path/to/vault
mnemosyne wikilinks audit
```

### Benchmark

```bash
mnemosyne benchmark run --suite suites/example.yaml --system hybrid --agent shape
mnemosyne benchmark compare benchmark-results/run-2026-04-07.json
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
.venv/bin/python -m ruff check mnemosyne/ tests/
```

Coverage: 80% (unit + integration). See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, architecture, and PR process.

---

## Prior Art & Acknowledgements

See [PRIOR_ART.md](PRIOR_ART.md). Key inspirations: [QMD](https://github.com/tobi/qmd) (tobi), [sqlite-vec](https://github.com/asg017/sqlite-vec) (Alex Garcia), OpenViking L0/L1/L2 tiered loading concept, Lilian Weng's [LLM Powered Autonomous Agents](https://lilianweng.github.io/posts/2023-06-23-agent/) memory taxonomy, BEIR hybrid retrieval benchmarks.

---

## Data Residency

Vault content is sent to Azure OpenAI for embedding and synthesis only. No data is stored externally. All vectors and entity data live in SQLite on your own infrastructure. See [EVALUATION.md](EVALUATION.md) and [SECURITY.md](SECURITY.md) for detail.

---

## Licence

MIT — see [LICENSE](LICENSE).
