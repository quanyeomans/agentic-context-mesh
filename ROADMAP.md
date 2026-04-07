# Roadmap

Agentic Context Mesh is a contextual retrieval platform for human-agent teams that keeps your knowledge private and on your own infrastructure. This document describes the current state, near-term priorities, and longer-term vision.

Discussion of priorities and feature direction happens in [GitHub Discussions → Roadmap & Priorities](https://github.com/quanyeomans/agentic-context-mesh/discussions).

---

## Why this exists

The dominant pattern for AI agent memory is to send your knowledge to a third-party LLM service. This creates three problems:

1. **Privacy** — your organisation's knowledge, decisions, and relationships leave your infrastructure
2. **Context quality** — generic retrieval without entity awareness, temporal reasoning, or team-specific patterns produces mediocre results
3. **Team coherence** — agents and humans draw from different sources, breaking the shared context that makes teams effective

Agentic Context Mesh is the alternative: a private, on-infrastructure retrieval layer that both human team members and AI agents query against the same indexed knowledge base. Your data never leaves your servers.

---

## Current state — v0.7.0

**NDCG@10 0.7756** on 263-case real-world benchmark suite (top-tier for heterogeneous personal knowledge bases — production RAG systems typically score 0.60–0.75).

| Capability | Status | Notes |
|---|---|---|
| Hybrid BM25 + vector search | ✅ Shipped | RRF fusion, concurrent dispatch |
| Entity graph + alias resolution | ✅ Shipped | 1160 entities, typed relationships |
| Multi-hop QueryPlanner | ✅ Shipped | LLM-decomposed sub-queries, parallel retrieval |
| Temporal query routing | ✅ Shipped | Date-aware chunking, timeline index |
| Session briefing synthesis | ✅ Shipped | GPT-4o-mini, 8-source concurrent pipeline |
| Auto-classification of writes | ✅ Shipped | Rule-based + LLM fallback |
| LLM-typed relationship enrichment | ✅ Shipped | Nightly cron, GPT-4o-mini batch classifier |
| Procedural path boost | ✅ Shipped | 1.4× re-rank for how-to/runbook paths, procedural NDCG 0.389 → 0.5542 |
| Contradiction detection | 🔲 Planned | v0.8.0 |
| Local/offline embedding | 🔲 Planned | v0.8.0 |
| REST API server mode | 🔲 Planned | v1.0.0 |

**Benchmark category breakdown (R1, 2026-04-07):**

| Category | NDCG@10 | Cases | Notes |
|---|---|---|---|
| entity | 0.823 | 47 | Strong — entity graph + alias resolution working |
| temporal | 0.810 | 39 | Strong — temporal routing + date-aware chunking |
| conceptual | 0.804 | 47 | Strong — vector search carrying semantic load |
| keyword | 0.800 | 32 | Strong — BM25 baseline solid |
| recall | 0.788 | 49 | Strong — known-document retrieval reliable |
| multi_hop | 0.728 | 33 | Good — QueryPlanner functional |
| **procedural** | **0.5542** | **30** | **Path boost shipped — target ≥ 0.55 met** |
| **Overall** | **0.7756** | **263** | |

---

## v0.7.0 — Procedural retrieval + contradiction detection

**Procedural retrieval target: NDCG ≥ 0.55 — ✅ Met (0.5542)**

Procedural queries ("how do I add a new agent", "what are the steps to configure X") were the weakest category. Root cause: files were being retrieved (Hit@5 = 0.533) but ranked at positions 4–7 rather than top-3. The fix is a path-aware re-ranking boost applied post-RRF.

**Retrieval-side:**
- [x] Procedural path boost — 1.4× `boosted_score` multiplier for documents whose path matches `how-to-*`, `/runbooks/`, `runbook-*`, or `procedure*` patterns. Applied after RRF and entity boost, gated to `PROCEDURAL` intent. Zero effect on other query types. See `mnemosyne/search/rrf.py:procedural_boost()`.
- [ ] Step-header chunking — split procedural docs by `##` step headers rather than fixed-size windows (next iteration for further improvement)

**Benchmark evidence (30-case procedural validation suite):**

| Metric | Before (v0.6.0) | After (v0.7.0) | Delta |
|---|---|---|---|
| procedural NDCG@10 | 0.389 | 0.5542 | +0.165 |
| procedural Hit@5 | 0.533 | 0.800 | +0.267 |

The Hit@5 improvement confirms the files were already being retrieved — the boost corrects ranking, not retrieval.

**Content-side:**
- [ ] Contribution guide for structuring procedural knowledge so it indexes well (this repo itself demonstrates the pattern)

**Contradiction detection (`mnemosyne contradict`):**
- [ ] On new memory write: compare against existing knowledge for direct contradictions
- [ ] Flag conflicts rather than silently overwriting — human-agent teams need to know when the knowledge base disagrees with itself
- [ ] CLI: `mnemosyne contradict check "<new content>"` → reports conflicting documents + confidence

**Good first issues for contributors:** step-based chunker, contradiction check prototype.

---

## v0.8.0 — Local model support

Removes Azure OpenAI as a hard dependency for embedding. Makes fully air-gapped deployment possible.

- [ ] Embedding provider abstraction layer — `EmbedProvider` protocol, Azure and local as implementations
- [ ] Ollama adapter — `nomic-embed-text`, `mxbai-embed-large` tested at 1536-dim parity
- [ ] Sentence-transformers adapter — for environments where Ollama isn't available
- [ ] Provider selection in `env.example` — `MNEMOSYNE_EMBED_PROVIDER=azure|ollama|sentence-transformers`

GPT-4o-mini usage (briefing, classify, benchmark judging) remains optional — all synthesis features degrade gracefully to rule-based fallbacks when no LLM is available.

---

## v1.0.0 — Stable public API

- [ ] REST/HTTP server mode — expose search, entity, and briefing as local endpoints (FastAPI, no external deps)
- [ ] Stable CLI contract — versioned with deprecation warnings before removal
- [ ] Multi-source support — plain markdown directories alongside QMD-indexed vaults
- [ ] Docker image — `ghcr.io/quanyeomans/agentic-context-mesh`

---

## Longer-term ideas (unscheduled)

These are directionally interesting but not committed. Raise a Discussion if any of these matter to you — community interest shapes scheduling.

- **Web UI** — local browser interface for entity graph visualisation and search exploration
- **Non-Obsidian sources** — Notion, Confluence, Logseq adapters
- **Multi-vault federation** — index and search across multiple knowledge bases with collection-level access control
- **Agent memory write API** — structured endpoint for agents to write observations back to the knowledge base with classification and contradiction checking
- **Evaluation dataset** — public benchmark suite with anonymised, domain-neutral queries (the current suite is vault-specific and private)

---

## How priorities are set

The project is maintained by [@quanyeomans](https://github.com/quanyeomans). Priorities are influenced by:

1. **Benchmark gaps** — categories with low NDCG@10 score get attention first
2. **Deployment blockers** — things that prevent new operators from adopting the system
3. **Community demand** — upvoted Discussions and well-articulated issues

If you want to influence the roadmap, the most effective approaches are:
- Open a Discussion in [Roadmap & Priorities](https://github.com/quanyeomans/agentic-context-mesh/discussions) with a concrete use case
- Submit a PR — working code with tests moves faster than feature requests
- Share benchmark results from your own deployment — real-world data changes priorities

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, testing standards, and PR process.
The issues labelled [`good first issue`](https://github.com/quanyeomans/agentic-context-mesh/issues?q=label%3A%22good+first+issue%22) are explicitly scoped for new contributors.
