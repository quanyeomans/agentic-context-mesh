# Mnemosyne

Hybrid memory retrieval system for [QMD](https://github.com/tobi/qmd) + Obsidian agent stacks. Extends the existing BM25 index with Azure OpenAI vector embeddings, entity graph, alias resolution, session briefing synthesis, and auto-classification of memory writes.

**Current status:** R1 complete (post-refactor benchmark, full gold rebuild). **NDCG@10 0.7756** on 263-case v2-real-world suite — entity **0.823**, recall **0.788**, multi_hop **0.728**. **Backlog:** Phase 8 (procedural NDCG improvement: 0.389 → target 0.55).

---

## Architecture

```
mnemosyne search "query" --agent builder
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
mnemosyne brief builder
       │
       ├─ Memory logs (last 7 days)
       ├─ Entity stubs (vault-entities collection)
       ├─ Rules + decisions (agent knowledge)
       ├─ Hybrid search (top queries)
       └─ GPT-4o-mini synthesis → briefing.md

mnemosyne classify "<content>"
       │
       ├─ Rule-based classifier (≥90% coverage)
       └─ GPT-4o-mini fallback → vault destination
```

---

## Module Status

| Phase | Module | Status | What it delivers |
|---|---|---|---|
| 0 | `mnemosyne embed` | ✅ Shipped | Azure OpenAI `text-embedding-3-large` → QMD sqlite-vec |
| 1 | `mnemosyne search` | ✅ Shipped | Hybrid BM25 + vector via RRF |
| 1 | `mnemosyne entity` | ✅ Shipped | Entity graph, alias resolution, entity boost |
| 2 | `mnemosyne temporal` | ✅ Shipped | Temporal query rewriting + date-aware chunking |
| 2 | `mnemosyne summarise` | ✅ Shipped | L0/L1 tiered context loading |
| 2 | `mnemosyne wikilinks` | ✅ Shipped | Wikilink injection + entity resolver |
| 3 | `mnemosyne brief` | ✅ Shipped | Session briefing synthesis via GPT-4o-mini |
| 3 | `mnemosyne classify` | ✅ Shipped | Auto-classification of memory writes |
| 3 | `mnemosyne benchmark` | ✅ Shipped | YAML-driven benchmark runner, phase gates |
| O-1 | `mnemosyne entity` (enhanced) | ✅ Shipped | Entity graph + multi-hop QueryPlanner with context injection |
| O-2 | `scripts/seed-entity-relations.py` | ✅ Shipped | LLM-typed relationship enrichment (GPT-4o-mini, nightly cron) |
| 4 | `mnemosyne contradict` | 🔲 Planned | Contradiction detection on writes |

Full design in [PRD.md](PRD.md).

---

## Benchmark Results

**Suite:** 263 cases across 7 categories (v2-real-world, NDCG@10). Scoring: NDCG@10 with LLM-as-judge (GPT-4o-mini) relevance grading. Suite rebuilt from scratch after vault refactor (R1, 2026-04-07).

### R1 results (current)

| Category | NDCG@10 | Cases |
|---|---|---|
| entity | 0.823 | 47 |
| recall | 0.788 | 49 |
| multi_hop | 0.728 | 33 |
| temporal | 0.810 | 39 |
| conceptual | 0.804 | 47 |
| keyword | 0.800 | 32 |
| procedural | 0.389 | 16 |
| **Overall** | **0.7756** | **263** |

### Score trajectory (NDCG@10 from Phase 5 onward)

| Run | NDCG@10 | Cases | Notes |
|---|---|---|---|
| BM25 baseline (Phase 0, weighted) | 0.389 | 43 | Pre-NDCG era; synthetic suite |
| Hybrid Phase 4 (weighted) | 0.6658 | 43 | Phase 0–4 synthetic suite |
| Phase 5 baseline | 0.3203 | 134 | First real-world suite; instrument issues |
| Phase 7-A recalibrated | 0.7690 | 252 | After instrument fix (768→1536 dim correction) |
| Phase 7-B 1536-dim | 0.7545 | 252 | Confirmed 1536-dim; keyword +0.114, entity +0.043 |
| O-1 entity-graph-planner | 0.7541 | 245 | multi_hop 0.716 (+0.035 vs P7-B) |
| **R1 post-refactor (current)** | **0.7756** | **263** | Gold suite rebuilt; vault fully re-indexed |

---

## Prerequisites

- **Python 3.10+**
- **QMD** (installed and indexed — `qmd index` must have run at least once to create `~/.cache/qmd/index.sqlite`)
- **sqlite-vec** extension (`.so`/`.dylib` on `SQLITE_VEC_PATH`, or auto-discovered by QMD)
- **Azure OpenAI** resource with:
  - `text-embedding-3-large` deployment (1536-dim)
  - `gpt-4o-mini` deployment (for briefing, classify, benchmark judging)
- **Azure Key Vault** (recommended) — or export secrets as environment variables directly

See [OPERATIONS.md](OPERATIONS.md) for full infrastructure setup, cron configuration, and first-run sequence.

## Install

```bash
git clone https://github.com/three-cubes/qmd-azure-embed /opt/mnemosyne
cd /opt/mnemosyne
python3 -m venv .venv
.venv/bin/pip install -e .
```

Then follow the [OPERATIONS.md first-run sequence](OPERATIONS.md#first-run-sequence).

---

## Usage

### Embed

```bash
export AZURE_OPENAI_API_KEY="..."
export AZURE_OPENAI_ENDPOINT="https://your-endpoint.cognitiveservices.azure.com/"
export AZURE_OPENAI_EMBED_DEPLOYMENT="text-embedding-3-large"

mnemosyne embed                  # incremental — pending docs only
mnemosyne embed --force          # full re-embed
mnemosyne embed --limit 50       # test with first 50 chunks
mnemosyne embed --changed        # re-embed recently modified files
```

### Search

```bash
mnemosyne search "what are our engineering patterns" --agent builder
mnemosyne search "last week's decisions" --agent shape --budget 3000
```

### Entity graph

```bash
mnemosyne entity list
mnemosyne entity lookup "alex jordan"
mnemosyne entity write --name "Alex Jordan" --type person
mnemosyne entity extract --collection vault-entities
```

### Session briefing

```bash
mnemosyne brief builder           # synthesise briefing for builder agent
mnemosyne brief shape --budget 5000
```

Output: `/data/mnemosyne/briefing/<agent>-latest.md`

### Auto-classification

```bash
mnemosyne classify "We decided to use PostgreSQL for the jobs table"
# → type: decision, destination: 04-Agent-Knowledge/builder/decisions.md, confidence: 0.95

mnemosyne classify "Use monkeypatching for Azure API calls in unit tests"
# → type: pattern, destination: 04-Agent-Knowledge/builder/patterns.md, confidence: 0.92
```

### Benchmark

```bash
mnemosyne benchmark run --suite suites/builder.yaml --system hybrid --agent shape
mnemosyne benchmark compare benchmark-results/B1-hybrid-2026-03-23.json
```

### Temporal

```bash
mnemosyne temporal index          # index date-tagged chunks
mnemosyne temporal query "decisions last week"
```

### Wikilinks

```bash
mnemosyne wikilinks inject --vault /data/obsidian-vault
mnemosyne wikilinks audit
```

Or use the deployment script (fetches secrets from Azure Key Vault at runtime):
```bash
./scripts/deploy.sh [--force] [--limit N]
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
.venv/bin/pytest tests/           # 685 passing, 16 skipped
.venv/bin/pytest tests/embed/     # embed module only
.venv/bin/python -m ruff check mnemosyne/ tests/
```

Coverage: 80% (unit + integration). See [CONTRIBUTING.md](CONTRIBUTING.md) for setup and architecture.

---

## Prior Art & Acknowledgements

See [PRIOR_ART.md](PRIOR_ART.md). Key inspirations: [QMD](https://github.com/tobi/qmd) (tobi), [sqlite-vec](https://github.com/asg017/sqlite-vec) (Alex Garcia), OpenViking L0/L1/L2 tiered loading concept, Lilian Weng's [LLM Powered Autonomous Agents](https://lilianweng.github.io/posts/2023-06-23-agent/) memory taxonomy.

---

## Data Residency

Vault content is sent to Azure OpenAI (Australia East) for embedding and synthesis only. No data is stored externally. All vectors and entity data live in SQLite on your own infrastructure. See [EVALUATION.md](EVALUATION.md) for full detail.

---

## Licence

MIT — see [LICENSE](LICENSE).
