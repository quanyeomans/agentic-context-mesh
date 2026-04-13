# Roadmap

Agentic Context Mesh is a contextual retrieval platform for human-agent teams that keeps your knowledge private and on your own infrastructure. This document describes the current state and near-term direction.

Discussion of priorities and feature direction happens in [GitHub Discussions → Roadmap & Priorities](https://github.com/quanyeomans/kairix/discussions).

---

## Why this exists

The dominant pattern for AI agent memory is to send your knowledge to a third-party LLM service. This creates three problems:

1. **Privacy** — your organisation's knowledge, decisions, and relationships leave your infrastructure
2. **Context quality** — generic retrieval without entity awareness, temporal reasoning, or team-specific patterns produces mediocre results
3. **Team coherence** — agents and humans draw from different sources, breaking the shared context that makes teams effective

Agentic Context Mesh is the alternative: a private, on-infrastructure retrieval layer that both human team members and AI agents query against the same indexed knowledge base. Your data never leaves your servers.

---

## Current state — v0.8.1

**NDCG@10 0.5686** on a 95-case curated real-world benchmark (strict NDCG@10, graded relevance gold labels). **Hit@5 0.87** — a relevant document in the top 5 for 87% of queries.

The v2 benchmark uses stricter NDCG@10 scoring with graded relevance (0/1/2) rather than the weighted category score used in earlier phases. See [EVALUATION.md](EVALUATION.md) for methodology details and full score trajectory.

| Capability | Status | Notes |
|---|---|---|
| Hybrid BM25 + vector search | ✅ Shipped | RRF fusion, concurrent dispatch, all intents |
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
| Contradiction detection | ✅ Shipped | `kairix contradict check` — hybrid search + LLM conflict detection |
| MCP server | ✅ Shipped | `kairix mcp serve` — search, entity, prep, timeline tools |
| Local/offline embedding | 🔲 Planned | v1.0.0 |
| REST API server mode | 🔲 Planned | v1.0.0 |

**Benchmark category breakdown (R10, 95-case suite, NDCG@10):**

| Category | NDCG@10 | Notes |
|---|---|---|
| entity | 0.751 | Entity graph + alias resolution |
| multi_hop | 0.549 | QueryPlanner functional |
| procedural | 0.564 | Path boost active |
| semantic | 0.519 | Hybrid vector load |
| keyword | 0.439 | Hybrid fix shipped in v0.8.1 (R11 expected improvement) |
| temporal | 0.535 | Date-filtered retrieval |
| **Overall** | **0.5686** | Hit@5 0.874, MRR@10 0.673 |

---

## Near-term

- **Retrieval quality** — R11 benchmark expected to show keyword improvement to ≥ 0.55 following the v0.8.1 hybrid fix; entity and multi_hop improvement as vault crawler populates entity graph
- **Local/offline embedding** — `EmbedProvider` abstraction; Ollama and sentence-transformers adapters (removes Azure OpenAI as hard dependency)
- **REST API** — FastAPI server mode exposing search, entity, and briefing as local HTTP endpoints
- **Docker image** — `ghcr.io/quanyeomans/kairix`

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
