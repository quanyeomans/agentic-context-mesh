# Changelog

All notable changes to this project will be documented in this file.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.9.3] - 2026-04-18 — kairix eval: automated evaluation suite generation

### Added
- **`kairix eval generate`** — GPL-inspired automated benchmark suite generation. Samples documents from the QMD corpus, prompts gpt-4o-mini to write retrieval queries, runs hybrid search, judges retrieved documents with graded relevance (0/1/2), and outputs a suite YAML. Based on Generative Pseudo Labeling (Wang et al. 2022, NAACL).
- **`kairix eval enrich`** — converts an existing suite's `gold_path`-based cases to graded `gold_titles`. Runs hybrid search and LLM judge for each case. Preserves all other case fields.
- **`kairix eval monitor`** — canary regression detection with rolling JSONL log. Flags when weighted NDCG drops >5% vs the 7-day rolling average. Exit code 2 on regression (distinct from exit code 1 hard failure). Designed for integration in `qmd-reindex.sh` after `kairix embed`.
- **`kairix eval report`** — generates a markdown trend report from the monitor log.
- **`kairix/eval/judge.py`** — per-document LLM relevance judge (gpt-4o-mini, 0/1/2 rubric, position-bias shuffle, 15-anchor calibration with `JudgeCalibrationError`).
- **`docs/evaluation-methodology.md`** — methodology with research citations: Cranfield paradigm, GPL, TREC-DL, position bias (Arabzadeh et al. 2024), NDCG formula.
- **`docs/eval-guide.md`** — user quickstart, command reference, monitoring setup, troubleshooting.

### Fixed
- Deployment process now uses tagged releases (`@v0.9.3`) rather than `@main` to make explicit which version is installed. `pip install git+...@main` silently skips reinstall when the version string is unchanged.

## [0.9.2] - 2026-04-15 — NDCG@10 in benchmark CLI output

### Changed
- **Benchmark CLI: NDCG@10 now shown in run summary** — `kairix benchmark run` now prints `NDCG@10`, `Hit@5`, and `MRR@10` directly below the weighted total when `ndcg`-scored cases are present in the suite. Previously these metrics were computed and stored in the result JSON but never displayed. NDCG@10 is the recommended metric for cross-run comparison; the weighted total continues to drive phase gate pass/fail logic.
- **Benchmark CLI: NDCG@10 delta in compare output** — `kairix benchmark compare A.json B.json` now shows a `NDCG@10 delta` row when both result files contain ndcg scores.
- `EVALUATION.md` — updated "Running the benchmark" section to show sample CLI output and clarify that NDCG@10 is the number to track across releases.

## [0.9.1] - 2026-04-15 — Apache 2.0, title-based qrels, Neo4j install script, deployment hardening

### Added
- **Benchmark: title-based document identity (TREC qrels pattern)** — `BenchmarkCase` now accepts `gold_title` (str) and `gold_titles` (list of `{title, relevance}` dicts) as the primary document identity for relevance judgments. Gold titles are stable note filename stems, decoupled from filesystem paths. A retrieved document matches if its filename stem normalises to the gold title, meaning benchmark scores are unaffected by vault reorganisation (files moved, folders renamed). New runner helpers: `_normalise_title()`, `_stem_from_path()`, `_title_in_retrieved()`, `_ndcg_score_by_title()`, `_hit_at_k_by_title()`, `_reciprocal_rank_by_title()`.
- **Benchmark: backwards compatibility** — existing suites using `gold_path`/`gold_paths` continue to work without modification. Path-based matching is retained as a fallback when `gold_titles`/`gold_title` are absent.
- **`kairix[neo4j]` optional dependency group** — `pip install "kairix[neo4j]"` installs the Neo4j Python driver (`neo4j>=5.0,<6.0`). Previously required a manual `pip install neo4j` step after deploy.
- **`check_secrets_loaded` two-tier check** — the deployment health check now probes the secrets file directly if env vars are absent. If the file exists and contains the required keys, the check returns OK with a note that credentials will activate on the next search call. This eliminates the false-negative on working VM deployments where secrets load lazily via `kairix._azure` import.
- **`scripts/install-neo4j.sh`** — Neo4j Community Edition install script. `--docker` (default): writes a minimal docker-compose.yml and starts `neo4j:5-community`. `--apt`: adds the Neo4j apt repository and installs via systemd. Both options print a GPL3 licence notice before proceeding, run `kairix onboard check` on completion.
- **`check_neo4j_reachable` improved fix hint** — now includes a `scripts/install-neo4j.sh` reference and a `docker run` one-liner for quick starts. Clarifies Neo4j is optional — entity boost and multi-hop are degraded without it.
- **`tests/onboard/test_check.py`** — deployment health check tests: Neo4j fix hint content assertions, secrets two-tier probe, vault root config, `run_all_checks` structural tests.

### Changed
- **Licence: MIT → Apache 2.0** — adds patent grant language. Better for commercial adoption and open-source ecosystem compatibility. `LICENSE` file replaced with full Apache 2.0 text. Copyright 2024-2026 quanyeomans contributors.
- `suites/example.yaml` — all cases migrated from `gold_paths` (path-based) to `gold_titles` (title-based). Documents are identified by their note slug, not their folder location.
- `EVALUATION.md` — methodology section rewritten to describe title-based qrels as the standard. Explains the TREC qrels convention, normalisation, and why title-based identity is correct for a living vault.
- `OPERATIONS.md` — cron section updated: replace inline `az keyvault secret show` with `source /run/secrets/kairix.env` (populated by `kairix-fetch-secrets.service`). Install instructions updated to `pip install kairix` / `pip install "kairix[neo4j]"`. New Neo4j section: optional dependency, install via `scripts/install-neo4j.sh`.
- `README.md` — install section updated to `pip install`; licence badge updated to Apache 2.0.
- `SECURITY.md` — rewritten to reflect current kairix architecture: tmpfs secrets via systemd oneshot unit, managed identity requirement, Neo4j GPL3 note, Apache 2.0 licence.

## [0.9.0] - 2026-04-14 — Sprint 7: Neo4j-native entity system + Docker sidecar secrets

### Added
- **W2**: Curator health (`kairix curator health`) rewritten to query Neo4j exclusively via Cypher. Reports entity counts, synthesis failures, missing vault_paths, and stale entities entirely from the graph — no SQLite dependency. `--no-neo4j` flag removed; client unavailability returns a graceful empty report.
- **W3**: entities.db retired (ADR-014). `kairix/entities/` package deleted in full. Neo4j is the sole canonical entity store. `kairix entity` CLI subcommand removed. All product code (`mcp/server.py`, `briefing/sources.py`, `curator/`) updated to use Neo4j queries only.
- **W4**: Docker sidecar secrets via Azure Key Vault. New `docker/vault-agent/` service: fetches five KV secrets at startup via `DefaultAzureCredential`, writes to tmpfs volume `/run/secrets/kairix.env` (chmod 600), signals readiness via `/run/secrets/.ready`. `kairix` service waits for `vault-agent: service_healthy` before starting.
- **W4**: `kairix/secrets.py` — `load_secrets(path)` reads a `KEY=VALUE` file into env vars without overwriting existing values. Called at module import in `kairix/_azure.py` and `kairix/graph/client.py`. Priority: existing env vars > sidecar secrets > KV subprocess calls.
- **W4**: `docker/docker-compose.yml` — three-service compose: vault-agent, kairix, neo4j:5-community. tmpfs secrets volume (`size=1m, mode=0700`) — secrets never written to disk.
- **W4**: `docker/.env.example` — template for `KAIRIX_KV_NAME`, Azure service principal, path mounts, and Neo4j config.

### Removed
- `kairix/entities/` — entire package (\_\_init\_\_.py, cli.py, schema.py, graph.py, extract.py, pipeline.py, reconcile.py, resolver.py, stop\_entities.py, migrations/001\_initial.sql)
- `tests/entities/` — all entity unit and integration tests
- `KAIRIX_TEST_DB` env var from CI workflows (no longer needed)
- `kairix entity` CLI subcommand

### Changed
- `kairix curator health` now requires a live Neo4j connection; `--no-neo4j` flag no longer accepted
- `kairix/mcp/server.py` `tool_entity()`: entities.db fallback removed; Neo4j miss returns `{"error": "Entity not found: <name>"}` directly
- `kairix/briefing/sources.py` `fetch_recent_decisions()`: entities.db query block removed; decisions sourced from vault only

### Benchmark (v0.9.0, 95 curated queries)
- entity NDCG 0.811 → **0.714** (vault evolution — new content Apr 13–14 shifted gold ranks; no-Neo4j baseline confirmed identical 0.714, ruling out code regression)
- keyword: 0.616 · procedural: 0.609 · temporal: 0.540 · multi_hop: 0.526 · semantic: 0.501
- **Overall NDCG@10: 0.587** · Hit@5: 0.821 · MRR@10: 0.679

---

## [0.8.1] - 2026-04-13 — Sprint 5–6: Benchmark Infrastructure + Entity Enrichment

### Added
- **CA-1**: `kairix curator health` — Curator agent health check CLI. Checks entities.db for synthesis failures (no summary), missing vault paths, and stale entities (configurable threshold, default 90 days). Reports Neo4j node counts when available. Output: vault-ready Markdown or JSON. Part of the Curator agent (ADR-003: plain Python, no framework dependency).
- **P1-2**: `kairix/llm/` — `LLMBackend` protocol with `chat()`, `embed()`, `embed_as_bytes()` methods. `AzureOpenAIBackend` and `AnthropicBackend` (stub) implementations. `get_default_backend()` returns `AzureOpenAIBackend`. All product code now receives `LLMBackend` via dependency injection rather than importing backends directly.
- **P1-3**: Repo boundary — all direct `kairix._azure` imports removed from product code. `hybrid.py` acquires embed via `_get_llm().embed_as_bytes()`. `search/planner.py` acquires chat via `_get_llm().chat()`. No module-level `kairix._azure` imports remain outside `kairix/llm/backends.py`.

### Fixed
- **TMP-7**: `vector_search_bytes()` now fetches `k × 4` candidates when a date filter is active. `VECTOR_DEFAULT_K=10` was too small for narrow date windows (e.g., "this week") — after force re-embed populated `chunk_date`, the top-10 candidates rarely included docs from a 7-day window, causing vec_count=0 for relative temporal queries.
- **KW-1**: All intents now dispatch BM25 + vector in parallel. Previously keyword intent ran BM25-only, causing vector-only docs to miss entirely. Keyword NDCG: 0.48 → **0.62** (+0.110).

### Benchmark (v0.8.1, 95 curated queries)
- keyword NDCG: 0.48 → **0.616** (hybrid fix)
- entity: **0.811** · procedural: 0.609 · temporal: 0.540 · multi_hop: 0.526 · semantic: 0.501
- **Overall NDCG@10: 0.603** · Hit@5: 0.821 · MRR@10: 0.669

## [0.8.0] - 2026-04-11 — Sprint 4: CRM Interaction Chunker + Temporal Benchmark Expansion

### Added
- **TMP-3**:  — Generic CRM interaction chunker. Processes JSON contact/interaction exports and writes one chunk file per interaction with injected frontmatter (date, contact, meeting_type). Enables CRM timelines to be embedded and searched with temporal filtering. 20 tests.
- **TMP-6**: Expanded temporal benchmark in  — 7 new cases (T02–T08) covering absolute date queries (T02–T05) and relative temporal expressions (T06–T08). Demonstrates correct behaviour: absolute date queries bypass date-range filter; relative expressions apply it.

### Notes
- The absolute-vs-relative temporal distinction (introduced in v0.7.0) is now validated with a broader case set.
- CRM interaction chunker is format-agnostic — adapt  to your CRM's export schema.

## [0.7.0] - 2026-04-10 — Sprint 3: Temporal Retrieval + Date Infrastructure

### Added
- **TMP-1**: `chunk_date` column in `content_vectors` — idempotent migration via `schema.py:ensure_vec_table`. Stores the date extracted from each chunk's source document.
- **TMP-1**: `kairix/embed/date_extract.py` — date extraction at embed time from (1) frontmatter `date`/`created`/`updated`/`created_at` fields (YYYY-MM-DD), (2) YYYY-MM year-month fields (mapped to first of month), (3) filename pattern `YYYY-MM-DD.md`. 24 tests.
- **TMP-2**: `get_date_filtered_paths(db, start, end)` in `embed/schema.py` — returns `frozenset[str]` of document paths with `chunk_date` in the given window. Used by `hybrid.py` for TEMPORAL intent date-range filtering.
- **TMP-2**: `is_relative_temporal(query)` in `temporal/rewriter.py` — returns `True` for relative temporal expressions (`last N days/weeks/months`, `recently`, `yesterday`, `today`, `this week/month`). Date filtering is only applied for relative expressions — absolute date references (`March 2026`, `2026-03-09`) query `about` a time period and must not be filtered by chunk_date.
- **TMP-2**: Date-filtered retrieval in `hybrid.py` — BM25 results post-filtered via `_path_from_file_uri()` + `date_filter_paths`; vector results post-filtered directly on `path`. Both fallback gracefully (no filter applied) when `date_filter_paths` is `None` or empty.
- **TMP-4**: `scripts/chunk-daily-files.py` — pre-processor for daily memory log files (`YYYY-MM-DD.md`). Splits on `##` headings, writes section chunks with injected frontmatter so each section inherits its parent document's date. 11 tests.
- **TMP-5**: `scripts/audit-date-formats.py` — scans Obsidian vault `.md` frontmatter for date field coverage. Classifies values as ISO / YYYY-MM (year-month) / non-ISO / absent. 13 tests.
- **TMP-5b**: YYYY-MM year-month frontmatter pattern in `date_extract.py` — maps `date: 2025-11` to `2025-11-01`. 6 additional tests.

### Fixed
- `kairix/embed/embed.py` — replaced hardcoded Key Vault name in error messages with `$KAIRIX_KV_NAME` env var reference.

### Benchmark (v0.7.0, 83 curated queries)
- temporal NDCG: 0.369 → **0.382** (TMP-2 date filtering for relative temporal expressions)
- entity: 0.751 · multi_hop: 0.549 · procedural: 0.564 · semantic: 0.519 · keyword: 0.439
- **Overall NDCG@10: 0.5569** · Hit@5: 0.84 · MRR: 0.67

## [0.6.0] - 2026-04-07 — Post-Refactor Benchmark + Relationship Enrichment

### Added
- O-2: `scripts/seed-entity-relations.py` LLM-typed relationship enrichment via GPT-4o-mini batch classifier
- O-2: Nightly cron (`0 3 * * * AEST`) — entity extract + relationship seed, Azure KV secret fetch
- O-2: `cron-scripts/cron-registry.json` entry for `entity-relation-seed`
- `scripts/build-eval-gold.py` — rebuilds benchmark gold suite from live search + LLM judge
- `suites/v2-real-world.yaml` — fully rebuilt gold suite (263 cases; collection-relative path format)
- Benchmark results: NDCG@10 **0.7756** (entity 0.823, recall 0.788, multi_hop 0.728, temporal 0.810, conceptual 0.804, keyword 0.800, procedural 0.389)
- OPERATIONS.md: comprehensive deployment guide (Azure prerequisites, Key Vault secrets, first-run sequence, cron setup, monitoring, troubleshooting)

### Fixed
- ADR-M08: Embed batch retry on QMD-induced dimension mismatch — `ensure_vec_table(db, actual_dims)` called per-batch on dimension error, retries once; handles `qmd-reindex-6h` cron overwriting `vectors_vec` mid-run
- Hourly embed cron: now fetches `AZURE_OPENAI_ENDPOINT` and `AZURE_OPENAI_API_KEY` from Azure Key Vault at runtime (managed identity)
- Gold suite paths: rebuilt to collection-relative format (matching `kairix search` output) after vault refactor broke 196/554 paths

### Benchmark
- NDCG@10 **0.7756** on 263-case suite (vault refactor fully indexed, gold paths rebuilt)
- Entity graph: 1160 entities, 112 typed relationships seeded
- Phase 8 target: procedural NDCG ≥ 0.55 (current 0.389)

---

## [0.5.3] - 2026-03-28 — Phase 7: 1536-dim Gold Recalibration

### Added
- Phase 7-A: Recalibrated benchmark instrument after discovering 768-dim baseline was measuring a broken config (extension load order caused silent 0-dim writes)
- Phase 7-B: Confirmed 1536-dim as correct operational config; rebuilt 252-case gold suite at correct dimensionality
- `scripts/run-benchmark-v2.py`: NDCG@10 scoring engine replacing weighted-total runner

### Benchmark
- Phase 7-A (768-dim, true baseline): NDCG@10 0.7690 on 252-case suite
- Phase 7-B (1536-dim operational): NDCG@10 0.7545 — keyword +0.114, entity +0.043 vs 768-dim

---

## [0.5.2] - 2026-03-26 — Phase 5–6: Real-World Eval Rebuild

### Added
- Phase 5: Replaced synthetic benchmark with real agent usage queries mined from server logs
- Phase 5: NDCG@10 scoring (was weighted category averages) — 134-case real-world suite
- Phase 6: Temporal routing fix — temporal queries routed to `kairix temporal query` before hybrid search
- Phase 6: Multi-hop pattern improvements — intermediate result reranking, entity bridging
- Phase 6: Suite expanded to 252 cases; multi-category NDCG scoring

### Benchmark
- Phase 5 initial (instrument issues): NDCG@10 0.3203 on 134-case suite
- Phase 6 (after instrument + temporal fix): NDCG@10 improved to 0.69+ range before recalibration

---

## [0.5.1] - 2026-04-06 — O-1: Entity Graph + Multi-Hop Planner

### Added
- O-1: Multi-hop QueryPlanner — GPT-4o-mini decomposes complex queries into sub-queries, parallel BM25+vector dispatch, result synthesis
- O-1: Entity graph seeded from vault-entities collection; entity boost wired into planner context injection
- O-1: `kairix entity extract --changed` incremental extraction pipeline
- O-1: `scripts/seed-entity-relations.py` (pattern-matching v1 — superseded by O-2 LLM classifier)

### Benchmark
- O-1: NDCG@10 0.7541 on 245-case suite — multi_hop 0.716 (+0.035 vs P7-B), entity 0.677

---

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
- Phase 3: `kairix brief <agent>` — 8-step concurrent briefing pipeline synthesises ~800-token session context from memory logs, entity stubs, rules, decisions, and hybrid search via GPT-4o-mini
- Phase 3: `kairix classify "<content>"` — two-stage auto-classification (rule-based first, LLM fallback) routes new writes to the correct vault file with confidence score
- `kairix/_azure.py`: `chat_completion()` for GPT-4o-mini synthesis calls
- `kairix/briefing/`: pipeline.py, sources.py, synthesiser.py, writer.py, cli.py — 48 tests
- `kairix/classify/`: rules.py, judge.py, router.py, cli.py — 83 tests
- Benchmark suite v1.1: CL01–CL04 classification cases; classification scoring in runner
- WORK-BREAKDOWN.md: implementation sequence, task breakdown, open questions
- Phase 2.5 PRD section: entity benchmark repair specification and approach
- ENGINEERING.md: entity failure-mode patterns, benchmark suite maintenance rules, gold-path validity rules

### Fixed
- LLM judge KV secret name: `azure-openai-gpt4o-mini-deployment` (was `azure-openai-deployment` — silent 0.0 scoring on all LLM-judged benchmark cases)
- RRF path dedup: `_canonical_path()` strips `qmd://collection-name/` prefix so BM25 and vector results for vault-entities stubs now merge correctly in fused dict
- Entity benchmark gold paths: E01–E06 now have `gold_path` + `score_method: exact` (was `null`/`llm` — LLM judge had no ground truth, scored 0.2–0.4 on tangential docs)
- Entity stub content: jordan-blake.md, acme-corp.md, platform.md enriched to 650–750 words; project-x.md to 490 words

### Benchmark
- Phase 3 gate (≥0.750): **PASS** — 0.762 weighted total
- entity: 0.300 → 0.933 (Phase 2.5 gold-path fix + stub enrichment)
- classification: 1.000 (4/4 rule-based, deterministic)
- recall: 0.875 (stable)
- Structural ceilings remaining: temporal (0.433 — Phase 4 date-aware chunking), multi_hop (0.480 — Phase 4 connected retrieval)

---

## [0.3.0] - 2026-03-23 — Phase 2.5: Entity Benchmark Repair

### Added
- Phase 2.5 entity stub enrichment: jordan-blake.md, acme-corp.md, platform.md, project-x.md enriched to ≥500 words
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
- Phase 1: kairix search CLI
- Phase 1b: entities.db schema + migration system
- Phase 1b: entity graph (write, lookup, mentions, relationships)
- Phase 1b: kairix entity CLI
- Benchmark CLI: YAML suite format, validate/run/compare/init commands
- Generalised benchmark framework SPEC.md
- CI: 4-stage pipeline, mypy strict, ruff, bandit, pip-audit, Dependabot
- ENGINEERING.md contributor guide

### Fixed
- sqlite-vec CTE pattern: MATCH must be primary table in inner CTE
- Collection scope: _SHARED_COLLECTIONS was missing vault (93% of content)
- Benchmark gold-pair validity: several benchmark gold pairs replaced with valid pairs

## [0.1.0] - 2026-03-22

### Added
- Phase 0: Azure OpenAI embedding pipeline (text-embedding-3-large, 1536-dim)
- Phase 0: schema validation + sqlite-vec extension loading
- Phase 0: staging table pattern for vec0 upserts
- Phase 0: recall gate (5/5 known-doc queries post-embed)
- Phase 0: kairix embed CLI
- Phase 0: 50-query benchmark runner (BM25 baseline: 0.5054)
- PRD v3.0 (68KB, 5 phases, 10 ADRs, benchmark gates)
- qmd_azure_embed shim for backwards compatibility

### Fixed
- Extension load order: load_sqlite_vec() before vec0 operations
- INSERT OR REPLACE not supported on vec0: use staging table pattern
- content.doc vs content.body: QMD schema uses doc not body
