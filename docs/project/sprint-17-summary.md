# Sprint 17 Summary — 2026-04-28/29

## Overview

Sprint 17 addressed the reference library NDCG@10 baseline regression (0.364) through five root causes: sqlite-vec KNN timeout, duplicate gold titles, missing collection scoping, hardcoded recall queries, and missing CLI for onboarding tools. The sprint expanded to include a comprehensive code quality review (43 findings), BDD test deepening (8 new feature files), domain-driven package restructure, and per-collection retrieval configuration.

## Outcome

| Metric | Before | After | Change |
|--------|--------|-------|--------|
| Reference library weighted | 0.364 | **0.687** | +89% |
| Reference library NDCG@10 | 0.364 | **0.679** | +87% |
| Reference library Hit@5 | unknown | **90.6%** | — |
| Phase 1 gate (0.62) | FAIL | **PASS** | |
| Phase 2 gate (0.68) | FAIL | **PASS** | |
| Vector search latency (32K) | 4-9s (sqlite-vec) | **1-3ms** (usearch) | ~1000x |
| Unit tests | 1,634 | **1,657** | +23 |
| BDD scenarios | 19 | **54** | +35 |
| MCP tools with BDD coverage | 0/6 | **4/6** | +4 |
| Code smells fixed | — | 19 (Tier 1-2) | — |

## Tracks Delivered

### Track A: usearch Vector Search Engine
- Replaced sqlite-vec brute-force KNN with usearch HNSW ANN index
- 194MB index, 32,337 vectors, memory-mapped persistence
- `VectorIndex` class with collection-scoped search, incremental add
- Immutability fix: `_ensure_mutable()` converts read-only mmap index before writes

### Track B: Path-Based Gold Identity
- Gold titles changed from stem-only ("readme") to path-relative ("engineering/adr-examples/readme")
- Fixes 243 duplicate title ambiguities in reference library
- Backwards compatible with stem-only matching
- Gold suite v2 generated on VM (143 queries, 974 candidates, LLM judge)

### Track C: Benchmark Collection Scoping
- `--collection` and `--fusion` flags on benchmark CLI and runner
- `search()` accepts `collections` parameter for explicit collection scoping
- `RetrievalConfig` extended with `bm25_limit`, `vec_limit`, `skip_vector`
- Hybrid sweep refactored: deleted 3 duplicated retrieval functions (~200 lines), calls main `search()` pipeline

### Track D: Onboarding CLI Wiring
- `kairix entity seed` — discover and seed entities from indexed documents
- `kairix eval auto-gold` — generate evaluation suite from corpus analysis
- `kairix eval tune` — recommend parameter changes from benchmark results
- Adaptive recall check — derives queries from indexed documents, replaces hardcoded defaults

### Track E: BDD Test Deepening
8 new feature files aligned with user outcomes:
1. MCP Agent Search (structured results, entity boost, collection scope)
2. MCP Agent Entity (known/unknown lookup, never-raises)
3. MCP Agent Timeline (temporal rewriting, date windows)
4. Benchmark Run (category scores, gates, NDCG metrics)
5. Eval Tune (recommendations from weak categories)
6. MCP Agent Prep (grounded synthesis, no-docs fallback)
7. Adaptive Recall Check (adaptive queries, degradation)
8. Eval Auto-Gold (corpus profiling, query proportioning)

Each scenario anchors a future OTel SLO metric.

### Track F: Code Quality Review
43 findings across all 22 refactoring.guru code smell categories:

**Tier 1 (Security + Confidentiality) — fixed:**
- str(exc) sanitised in 5 locations
- Cypher injection hardened (int cast)
- Real Azure KV name, private repo refs, confidential variable names cleaned
- CLAUDE.md gitignored

**Tier 2 (Resource Leaks + Dead Code + Duplicates) — fixed:**
- DB connection leaks in embed CLI
- Dead code removed (_open_vec_db, duplicate mkdir, vector.py)
- Duplicate constants consolidated (VALID_AGENTS, REGRESSION_THRESHOLD, ensure_vec_table)

**Tier 3 (Structural) — documented for future sprints:**
- Duplicate DCG implementations, category registry shotgun surgery
- 3 incompatible embedding protocols, BenchmarkCase temporary fields
- Feature envy in research/nodes.py, 7 parallel node classes without base

### Stream 2: Legacy Cleanup
- Deleted `qmd_azure_embed/`, `mnemosyne.egg-info/`
- 50+ mnemosyne/qmd references removed across 51 files
- CI workflows cleaned (no more QMD install steps)
- `bm25-qmd` renamed to `bm25-filepath`

### Stream 3: Domain-Driven Package Structure
22 flat packages reorganised into 5 bounded contexts:
- **core/** — search, embed, db, temporal, classify
- **knowledge/** — entities, graph, store, wikilinks, reflib, summaries, contradict
- **agents/** — mcp, briefing, curator, research
- **quality/** — benchmark, eval, contracts
- **platform/** — setup, onboard, llm

335 import rewrites across 221 files. Automated via migration script.

### Per-Collection Retrieval Config
- YAML schema for per-collection retrieval overrides
- Resolution chain: explicit > reflib hardcoded > per-collection YAML > global > defaults
- Reference library baseline checked into repo (bm25_primary, vec_limit=5)
- `search()` auto-resolves per-collection config

## Lessons Learned

1. **Discuss architecture before executing.** Multiple times during the sprint, changes were made without waiting for review. The per-collection config, domain restructure, and code quality fixes all benefited from pausing to plan first.

2. **Don't maintain backwards compatibility for internal code.** The sqlite-vec removal was delayed by shims and soft-fail patterns. The clean break (delete vector.py, remove all sqlite-vec code) was faster and cleaner.

3. **DRY violations compound.** The hybrid sweep had its own copy of the entire retrieval pipeline. When sqlite-vec was replaced with usearch in the main pipeline, the sweep's copy didn't get updated — causing all vector searches to fail silently. The fix was to delete the duplicate and call the main pipeline.

4. **BDD scenarios should test user outcomes, not implementation.** The existing BDD tests duplicated contract tests in Gherkin. The new scenarios focus on what operators and agents observe: "I run the benchmark and see gates", "the agent calls tool_search and gets structured results."

5. **The test pyramid matters.** At 93% unit / 1.5% BDD, regressions at module boundaries went undetected. The BDD deepening specifically targets cross-module outcomes that unit tests can't catch.

6. **Rate limiting dominates embed cost.** The 32K-chunk re-embed took ~2 hours due to Azure 429s, not computation. The actual embed time per batch is <3s; the wait between batches is 30-60s.

## Cost

- Azure embedding API: ~$1.68 (two full embeds at $0.84 each)
- LLM judge for gold suite: ~$0.20 (1,948 judge calls)
- Total Azure API cost: ~$1.88

## Files Changed

- 275 files in domain restructure
- 51 files in legacy cleanup
- ~30 files in Tracks A-D
- 25 files in BDD deepening (Track E)
- ~20 files in code quality fixes (Track F)
