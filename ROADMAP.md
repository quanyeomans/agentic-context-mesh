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

## Current state — v0.9.0

**NDCG@10 0.587** on a 95-case curated real-world benchmark (strict NDCG@10, graded relevance gold labels). **Hit@5 0.821** — a relevant document in the top 5 for 82% of queries.

The benchmark uses strict NDCG@10 scoring with graded relevance (0/1/2). See [EVALUATION.md](EVALUATION.md) for methodology and scoring interpretation.

| Capability | Status | Notes |
|---|---|---|
| Hybrid BM25 + vector search | ✅ Shipped | RRF fusion, concurrent dispatch, all intents |
| Entity graph + alias resolution | ✅ Shipped | Neo4j canonical store, typed relationships |
| Multi-hop QueryPlanner | ✅ Shipped | LLM-decomposed sub-queries, parallel retrieval |
| Temporal query routing | ✅ Shipped | Date-aware chunking, timeline index |
| Session briefing synthesis | ✅ Shipped | GPT-4o-mini, 8-source concurrent pipeline |
| Auto-classification of writes | ✅ Shipped | Rule-based + LLM fallback |
| LLM-typed relationship enrichment | ✅ Shipped | Nightly cron, GPT-4o-mini batch classifier |
| Procedural query boost | ✅ Shipped | Path-weighted re-rank for how-to and runbook queries |
| Neo4j graph layer | ✅ Shipped | Community Edition, Bolt, `kairix.graph` module |
| Vault crawler | ✅ Shipped | Entity discovery from vault structure |
| LLM backend abstraction | ✅ Shipped | `LLMBackend` protocol, `AzureOpenAIBackend` adapter |
| Deployment-specific collections | ✅ Shipped | `KAIRIX_EXTRA_COLLECTIONS` env var |
| Contradiction detection | ✅ Shipped | `kairix contradict check` — hybrid search + LLM conflict detection |
| MCP server | ✅ Shipped | `kairix mcp serve` — search, entity, prep, timeline tools |
| Neo4j-native entity health | ✅ Shipped | Curator health reports entirely from Neo4j Cypher queries |
| entities.db retirement | ✅ Shipped | SQLite entity store removed; Neo4j is sole canonical store |
| Docker sidecar secrets | ✅ Shipped | vault-agent sidecar fetches Azure KV secrets to tmpfs at startup |
| Cross-encoder re-ranking | 🔲 Planned | Post-RRF semantic re-rank via `ms-marco-MiniLM`; targets semantic NDCG |
| Incremental file watcher | 🔲 Planned | `watchfiles`-based daemon; sub-60s vault sync latency |
| Automated benchmark regression gate | 🔲 Planned | CI gate blocking merges that regress NDCG@10 by > 0.02 |
| Observability dashboard | 🔲 Planned | Per-query latency, cache hit rate, entity graph density metrics |
| Search result feedback loop | 🔲 Planned | Thumbs-up/down signals to retrain gold suite and adjust weights |
| Multi-user isolation | 🔲 Planned | Per-agent Neo4j namespace, collection-level access control |
| Streaming search response | 🔲 Planned | Server-sent events for MCP and REST consumers |
| Webhook / push indexing | 🔲 Planned | HTTP endpoint to trigger incremental embed on external write events |
| Local/offline embedding | 🔲 Planned | `EmbedProvider` abstraction; Ollama + sentence-transformers adapters |
| REST API server mode | 🔲 Planned | FastAPI server mode exposing search, entity, and briefing as HTTP endpoints |

**Benchmark category breakdown (95-case suite, NDCG@10):**

| Category | NDCG@10 | Notes |
|---|---|---|
| entity | 0.714 | Vault evolution shifted gold ranks (not a code regression — see EVALUATION.md) |
| keyword | 0.616 | Hybrid BM25 + vector fix shipped in v0.8.1 |
| procedural | 0.609 | Path boost active |
| temporal | 0.540 | Date-filtered retrieval |
| multi_hop | 0.526 | QueryPlanner functional |
| semantic | 0.501 | Weakest category — cross-encoder re-ranking is the primary lever |
| **Overall** | **0.587** | Hit@5 0.821, MRR@10 0.679 |

---

## Near-term

- **Cross-encoder re-ranking** — post-RRF re-ranking pass using `ms-marco-MiniLM-L-6-v2`. Primary target: semantic NDCG (0.501 → 0.60+ expected). Opt-in flag `--rerank` initially; on by default once latency benchmarked. See [docs/planning/rerank-cross-encoder.md](docs/planning/rerank-cross-encoder.md).
- **File watcher** — `watchfiles`-based daemon replacing the 60-second embed cron. Vault changes embedded within seconds of write, reducing lag for session-prep queries against recently-added content. See [docs/planning/file-watcher.md](docs/planning/file-watcher.md).
- **Automated benchmark regression gate** — `benchmark-gate.yml` extended to block PRs that regress overall NDCG@10 by more than 0.02 against a pinned baseline. Eliminates silent score degradation from retrieval changes.
- **Observability dashboard** — structured JSON log output (`LOG_LEVEL=json`) parsed by a lightweight dashboard. Per-query latency, intent distribution, entity hit rate, RRF score distributions surfaced without third-party tooling.
- **Local/offline embedding** — `EmbedProvider` abstraction; Ollama and sentence-transformers adapters (removes Azure OpenAI as hard dependency for non-Azure deployments)
- **REST API** — FastAPI server mode exposing search, entity, and briefing as local HTTP endpoints

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
