# Mnemosyne

Hybrid memory retrieval system for [QMD](https://github.com/tobi/qmd) + Obsidian agent stacks. Extends the existing BM25 index with Azure OpenAI vector embeddings, entity graph, alias resolution, session briefing synthesis, and auto-classification of memory writes.

**Current status:** Phase 3 shipped. Benchmark: **0.762** weighted total on 43-query suite across 7 categories. All three phase gates passed.

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
| 4 | `mnemosyne contradict` | 🔲 Planned | Contradiction detection on writes |

Full design in [PRD.md](PRD.md).

---

## Benchmark Results

**Suite:** 43 queries across 7 categories. Scoring: exact path match for entity/recall cases; LLM-as-judge (GPT-4o-mini) for conceptual/temporal/multi-hop/procedural.

| Category | Weight | Score | Status |
|---|---|---|---|
| Recall | 25% | 0.875 | ✅ |
| Temporal | 20% | 0.433 | ⚠️ structural ceiling — Phase 4 |
| Entity | 20% | 0.933 | ✅ |
| Conceptual | 15% | 0.500 | ✅ |
| Multi-hop | 10% | 0.480 | — |
| Procedural | 10% | 0.400 | — |
| Classification | 15% | 1.000 | ✅ |
| **Weighted total** | | **0.762** | |

### Phase gates

| Gate | Threshold | Score | Result |
|---|---|---|---|
| Phase 1 | ≥ 0.620 | 0.655 | ✅ PASSED |
| Phase 2 | ≥ 0.680 | 0.762 | ✅ PASSED |
| Phase 3 | ≥ 0.750 | 0.762 | ✅ PASSED |
| Phase 4 | ≥ 0.800 | — | Pending |

### Score trajectory

| System | Weighted total |
|---|---|
| BM25 baseline (Phase 0) | 0.389 |
| Hybrid Phase 1 (first run) | 0.558 |
| Hybrid Phase 2.5 (entity fix) | 0.655 |
| **Hybrid Phase 3 (current)** | **0.762** |

Temporal (0.433) and multi-hop (0.480) are structural ceilings — they require date-aware chunking (Phase 4), not search tuning. Classification (1.000) is deterministic rule-based.

---

## Install

```bash
git clone https://github.com/three-cubes/qmd-azure-embed /tmp/mnemosyne
cd /tmp/mnemosyne
python3 -m venv .venv
.venv/bin/pip install -e .
```

Requires: Python 3.10+, QMD with sqlite-vec, Azure OpenAI API access (embedding + GPT-4o-mini).

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
