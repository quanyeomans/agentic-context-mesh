# Changelog

All notable changes to this project will be documented in this file.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

*No unreleased changes. Phase 4 (contradiction detection, date-aware chunking) is planned.*

## [0.5.0] - 2026-03-23 — Phase 2: Temporal + Summaries + Wikilinks

### Added
- Phase 2: temporal chunker + query rewriter + timeline CLI
- Phase 2: L0/L1 summaries generation (gpt-4o-mini) + tier router
- Phase 2: wikilink injector + entity resolver + audit CLI (ADR-M07)
- Phase 2: entity NER extraction pipeline + ontology reconciler
- entities.db schema v2: vault_path, status, frequency, relationships table
- Raw query logging: MNEMOSYNE_LOG_QUERIES=1 → queries.jsonl
- scripts/analyze_queries.py: real query distribution analysis
- Keyword zero-result fallback to vector search
- Entity vec_failed fix: KV timeout 15s → 30s

### Fixed
- Vector index re-embedded at 1536-dim (was 768-dim QMD native — vectors never landed in vectors_vec)
- KV cold-start causing entity vector search failures (20-45% failure rate)
- Keyword queries returning 0 results when BM25 returns empty

## [0.4.0] - 2026-03-23 — Phase 3: Briefing + Classification

### Added
- Phase 3: `mnemosyne brief <agent>` — 8-step concurrent briefing pipeline synthesises ~800-token session context from memory logs, entity stubs, rules, decisions, and hybrid search via GPT-4o-mini
- Phase 3: `mnemosyne classify "<content>"` — two-stage auto-classification (rule-based first, LLM fallback) routes new writes to the correct vault file with confidence score
- `mnemosyne/_azure.py`: `chat_completion()` for GPT-4o-mini synthesis calls
- `mnemosyne/briefing/`: pipeline.py, sources.py, synthesiser.py, writer.py, cli.py — 48 tests
- `mnemosyne/classify/`: rules.py, judge.py, router.py, cli.py — 83 tests
- Benchmark suite v1.1: CL01–CL04 classification cases; classification scoring in runner
- WORK-BREAKDOWN.md: implementation sequence, task breakdown, open questions
- Phase 2.5 PRD section: entity benchmark repair specification and approach
- ENGINEERING.md: entity failure-mode patterns, benchmark suite maintenance rules, gold-path validity rules

### Fixed
- LLM judge KV secret name: `azure-openai-gpt4o-mini-deployment` (was `azure-openai-deployment` — silent 0.0 scoring on all LLM-judged benchmark cases)
- RRF path dedup: `_canonical_path()` strips `qmd://collection-name/` prefix so BM25 and vector results for vault-entities stubs now merge correctly in fused dict
- Entity benchmark gold paths: E01–E06 now have `gold_path` + `score_method: exact` (was `null`/`llm` — LLM judge had no ground truth, scored 0.2–0.4 on tangential docs)
- Entity stub content: alex-jordan.md, triad-consulting.md, openclaw.md enriched to 650–750 words; tc-productivity.md to 490 words

### Benchmark
- Phase 3 gate (≥0.750): **PASS** — 0.762 weighted total
- entity: 0.300 → 0.933 (Phase 2.5 gold-path fix + stub enrichment)
- classification: 1.000 (4/4 rule-based, deterministic)
- recall: 0.875 (stable)
- Structural ceilings remaining: temporal (0.433 — Phase 4 date-aware chunking), multi_hop (0.480 — Phase 4 connected retrieval)

---

## [0.3.0] - 2026-03-23 — Phase 2.5: Entity Benchmark Repair

### Added
- Phase 2.5 entity stub enrichment: alex-jordan.md, triad-consulting.md, openclaw.md, tc-productivity.md enriched to ≥500 words
- Gold paths added to entity benchmark cases E01–E06

### Fixed
- Entity score collapse (0.733→0.300): root cause — benchmark gold_path: null + sparse stub content
- Phase 1 YAML suite gate re-confirmed: 0.655 (entity 0.300 → 0.933 after fix)


## [0.2.0] - 2026-03-22

### Added
- Phase 1: intent classifier (keyword/semantic/temporal/entity/procedural)
- Phase 1: BM25 wrapper (qmd subprocess → structured results)
- Phase 1: vector search wrapper (sqlite-vec CTE MATCH)
- Phase 1: RRF fusion + entity boost
- Phase 1: token budget enforcer (L0/L1/L2 tiers)
- Phase 1: hybrid orchestrator + parallel dispatch
- Phase 1: mnemosyne search CLI
- Phase 1b: entities.db schema + migration system
- Phase 1b: entity graph (write, lookup, mentions, relationships)
- Phase 1b: mnemosyne entity CLI
- Benchmark CLI: YAML suite format, validate/run/compare/init commands
- Generalised benchmark framework SPEC.md
- CI: 4-stage pipeline, mypy strict, ruff, bandit, pip-audit, Dependabot
- ENGINEERING.md contributor guide

### Fixed
- sqlite-vec CTE pattern: MATCH must be primary table in inner CTE
- Collection scope: _SHARED_COLLECTIONS was missing vault (93% of content)
- Benchmark gold-pair validity: R03/R04/R06/R07/R08 replaced with valid pairs

## [0.1.0] - 2026-03-22

### Added
- Phase 0: Azure OpenAI embedding pipeline (text-embedding-3-large, 1536-dim)
- Phase 0: schema validation + sqlite-vec extension loading
- Phase 0: staging table pattern for vec0 upserts
- Phase 0: recall gate (5/5 known-doc queries post-embed)
- Phase 0: mnemosyne embed CLI
- Phase 0: 50-query benchmark runner (BM25 baseline: 0.5054)
- PRD v3.0 (68KB, 5 phases, 10 ADRs, benchmark gates)
- qmd_azure_embed shim for backwards compatibility

### Fixed
- Extension load order: load_sqlite_vec() before vec0 operations
- INSERT OR REPLACE not supported on vec0: use staging table pattern
- content.doc vs content.body: QMD schema uses doc not body
