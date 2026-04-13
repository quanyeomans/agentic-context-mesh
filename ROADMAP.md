# Roadmap

Agentic Context Mesh is a contextual retrieval platform for human-agent teams that keeps your knowledge private and on your own infrastructure. This document describes the current state, near-term priorities, and longer-term vision.

Discussion of priorities and feature direction happens in [GitHub Discussions → Roadmap & Priorities](https://github.com/quanyeomans/kairix/discussions).

---

## Why this exists

The dominant pattern for AI agent memory is to send your knowledge to a third-party LLM service. This creates three problems:

1. **Privacy** — your organisation's knowledge, decisions, and relationships leave your infrastructure
2. **Context quality** — generic retrieval without entity awareness, temporal reasoning, or team-specific patterns produces mediocre results
3. **Team coherence** — agents and humans draw from different sources, breaking the shared context that makes teams effective

Agentic Context Mesh is the alternative: a private, on-infrastructure retrieval layer that both human team members and AI agents query against the same indexed knowledge base. Your data never leaves your servers.

---

## Current state — v0.8.0

**NDCG@10 0.5686** on a 95-case curated real-world benchmark (strict NDCG@10, graded relevance gold labels). **Hit@5 0.87** — a relevant document in the top 5 for 87% of queries.

The v2 benchmark uses stricter NDCG@10 scoring with graded relevance (0/1/2) rather than the weighted category score used in earlier phases. See [EVALUATION.md](EVALUATION.md) for methodology details and full score trajectory.

| Capability | Status | Notes |
|---|---|---|
| Hybrid BM25 + vector search | ✅ Shipped | RRF fusion, concurrent dispatch |
| Entity graph + alias resolution | ✅ Shipped | Curated entities, typed relationships |
| Multi-hop QueryPlanner | ✅ Shipped | LLM-decomposed sub-queries, parallel retrieval |
| Temporal query routing | ✅ Shipped | Date-aware chunking, timeline index |
| Session briefing synthesis | ✅ Shipped | GPT-4o-mini, 8-source concurrent pipeline |
| Auto-classification of writes | ✅ Shipped | Rule-based + LLM fallback |
| LLM-typed relationship enrichment | ✅ Shipped | Nightly cron, GPT-4o-mini batch classifier |
| Procedural query boost | ✅ Shipped | Path-weighted re-rank for how-to and runbook queries |
| Entity graph quality tooling | ✅ Shipped | Stub validator, enrichment pipeline, regression runner |
| Neo4j graph layer | ✅ Shipped | Community Edition, Bolt, `kairix.graph` module |
| Vault crawler | ✅ Shipped | ADR-014 entity discovery from vault structure |
| Entity health check | ✅ Shipped | `kairix curator health` — synthesis failures, staleness, missing vault paths |
| LLM backend abstraction | ✅ Shipped | `LLMBackend` protocol, `AzureOpenAIBackend` adapter |
| Deployment-specific collections | ✅ Shipped | `KAIRIX_EXTRA_COLLECTIONS` env var |
| Contradiction detection | 🔲 Planned | v0.9.0 |
| Local/offline embedding | 🔲 Planned | v1.0.0 |
| REST API server mode | 🔲 Planned | v1.0.0 |

**Benchmark category breakdown (R10, 83-case suite, NDCG@10):**

| Category | NDCG@10 | Notes |
|---|---|---|
| entity | 0.751 | Entity graph + alias resolution |
| multi_hop | 0.549 | QueryPlanner functional |
| procedural | 0.564 | Path boost active |
| semantic | 0.519 | Hybrid vector load |
| keyword | 0.439 | Hybrid fix shipped (R11 expected improvement) |
| temporal | 0.535 | Date-filtered retrieval (TMP-2) |
| **Overall** | **0.5686** | Hit@5 0.874, MRR@10 0.673 |

---

## v0.9.0 — Retrieval quality uplift + keyword fix

**Keyword hybrid fix (shipped in v0.8.1):**
- [x] KEYWORD intent now runs full BM25 + vector hybrid (was BM25-only)
- [x] Root cause: skip_vector=True for KEYWORD halved RRF scores and triggered serial fallback
- Expected: keyword NDCG ≥ 0.55 in R11 (was 0.439 in R10)

**Entity graph vault_path population:**
- [ ] Run `kairix vault crawl` on TC vault → populate Neo4j org/person nodes with vault_path
- [ ] Verify `kairix vault health` reports ok: true (orgs_missing_vault_path = 0)
- [ ] Re-run R11 benchmark — hypothesis: entity NDCG improves as entity boost resolves correctly
- [ ] Target: entity NDCG ≥ 0.75 maintained; multi_hop NDCG ≥ 0.60

**Entity summary synthesis:**
- [ ] Populate `summary` field on Neo4j nodes from vault stub body text
- [ ] Target: summary coverage ≥ 80% (per SLO.md)

**Contradiction detection (shipped in v0.8.0):**
- [x] `kairix contradict check "<new content>"` — hybrid search + LLM conflict detection
- [x] Configurable threshold and top-k, JSON output

---

## v0.10.0 — Observability + SLO instrumentation

**Latency tracking:**
- [ ] Aggregate `SearchResult.latency_ms` into a rolling P50/P95 log in `KAIRIX_SEARCH_LOG`
- [ ] `kairix search stats` — display P50/P95/P99 from last N queries
- [ ] Alert threshold: P95 > 2,000ms logs a WARNING to the embed log

**Vault health time series:**
- [ ] `kairix vault health` writes a JSON snapshot to `KAIRIX_DATA_DIR/health-history.jsonl`
- [ ] `kairix vault health --trend` — display deltas vs last snapshot (entity count growth, missing vault_path change)

**Benchmark CI gate:**
- [ ] `kairix benchmark run --suite suites/example.yaml` runs in CI on every PR touching search/entity/temporal/embed
- [ ] Hard floor per category (see SLO.md §1) blocks merge when breached

**Agent feedback loop (telemetry opt-in):**
- [ ] `search_outcome` event field in `KAIRIX_SEARCH_LOG` — agents can emit useful/not_useful/no_results
- [ ] `kairix search feedback` CLI for interactive sessions
- [ ] Correlate NDCG@10 against feedback rate to validate benchmark relevance

See [SLO.md](SLO.md) for full target definitions and measurement methods.

---

## v1.0.0 — Local model support + stable public API

**Local embedding (removes Azure OpenAI as hard dependency):**
- [ ] Embedding provider abstraction layer — `EmbedProvider` protocol, Azure and local as implementations
- [ ] Ollama adapter — `nomic-embed-text`, `mxbai-embed-large` tested at 1536-dim parity
- [ ] Sentence-transformers adapter — for environments where Ollama isn't available
- [ ] Provider selection in `env.example` — `KAIRIX_EMBED_PROVIDER=azure|ollama|sentence-transformers`

**Stable public API:**
- [ ] REST/HTTP server mode — expose search, entity, and briefing as local endpoints (FastAPI, no external deps)
- [ ] Stable CLI contract — versioned with deprecation warnings before removal
- [ ] Multi-source support — plain markdown directories alongside QMD-indexed vaults
- [ ] Docker image — `ghcr.io/quanyeomans/kairix`

GPT-4o-mini usage (briefing, classify, benchmark judging) remains optional — all synthesis features degrade gracefully to rule-based fallbacks when no LLM is available.

---

## Longer-term ideas (unscheduled)

These are directionally interesting but not committed. Raise a Discussion if any of these matter to you — community interest shapes scheduling.

- **Web UI** — local browser interface for entity graph visualisation and search exploration
- **Non-Obsidian sources** — Notion, Confluence, Logseq adapters
- **Multi-vault federation** — index and search across multiple knowledge bases with collection-level access control
- **Agent memory write API** — structured endpoint for agents to write observations back to the knowledge base with classification and contradiction checking
- **Evaluation dataset** — public benchmark suite with anonymised, domain-neutral queries (the current suite is vault-specific and private)
- **Temporal reasoning improvement** — recency decay function, temporal relationship edges with `valid_from`/`valid_to`, decision/fact extraction

---

## How priorities are set

The project is maintained by [@quanyeomans](https://github.com/quanyeomans). Priorities are influenced by:

1. **Benchmark gaps** — categories with low NDCG@10 score get attention first
2. **Deployment blockers** — things that prevent new operators from adopting the system
3. **Community demand** — upvoted Discussions and well-articulated issues

If you want to influence the roadmap, the most effective approaches are:
- Open a Discussion in [Roadmap & Priorities](https://github.com/quanyeomans/kairix/discussions) with a concrete use case
- Submit a PR — working code with tests moves faster than feature requests
- Share benchmark results from your own deployment — real-world data changes priorities

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, testing standards, and PR process.
The issues labelled [`good first issue`](https://github.com/quanyeomans/kairix/issues?q=label%3A%22good+first+issue%22) are explicitly scoped for new contributors.
