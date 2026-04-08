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
| Entity graph + alias resolution | ✅ Shipped | Curated entities, typed relationships |
| Multi-hop QueryPlanner | ✅ Shipped | LLM-decomposed sub-queries, parallel retrieval |
| Temporal query routing | ✅ Shipped | Date-aware chunking, timeline index |
| Session briefing synthesis | ✅ Shipped | GPT-4o-mini, 8-source concurrent pipeline |
| Auto-classification of writes | ✅ Shipped | Rule-based + LLM fallback |
| LLM-typed relationship enrichment | ✅ Shipped | Nightly cron, GPT-4o-mini batch classifier |
| Procedural query boost | ✅ Shipped | Path-weighted re-rank for how-to and runbook queries |
| Entity graph quality tooling | ✅ Shipped | Stub validator, enrichment pipeline, regression runner |
| Contextual prep intent | 🔲 Planned | v0.8.0 — `mnemosyne prep` command |
| Contradiction detection | 🔲 Planned | v0.9.0 |
| Local/offline embedding | 🔲 Planned | v1.0.0 |
| REST API server mode | 🔲 Planned | v1.0.0 |

**Benchmark category breakdown (current):**

| Category | NDCG@10 | Cases | Notes |
|---|---|---|---|
| entity | 0.823 | 47 | Strong — entity graph + alias resolution working |
| temporal | 0.810 | 39 | Strong — temporal routing + date-aware chunking |
| conceptual | 0.804 | 47 | Strong — vector search carrying semantic load |
| keyword | 0.800 | 32 | Strong — BM25 baseline solid |
| recall | 0.788 | 49 | Strong — known-document retrieval reliable |
| multi_hop | 0.728 | 33 | Good — QueryPlanner functional |
| procedural | 0.554 | 16 | ✅ Shipped — path boost raised from 0.389 |
| **Overall** | **0.7756** | **263** | |

---

## v0.8.0 — Contextual preparation intent

The next capability milestone is moving from ranked retrieval to structured contextual preparation — answering queries like "prepare me for a meeting with [organisation]" or "what should I know before reaching out to [person]".

This requires extending the entity graph with richer attributes and adding a new retrieval intent that traverses entity relationships rather than just returning ranked documents.

**Entity graph expansion:**
- [ ] Entity quality tooling — stub validator, enrichment pipeline, regression runner (ships in v0.7.x)
- [ ] Extended stub schema — `industry`, `geography`, `tier`, `stakeholder_personas` for organisations; `org`, `role`, `interests`, `last_interaction` for persons
- [ ] Entity mining from vault structure — discover entity candidates from directory structure, output draft stubs for human review
- [ ] Resource cross-referencing — add `related-entities:` frontmatter to research documents for entity traversal

**CONTEXTUAL_PREP retrieval intent:**
- [ ] New intent class — routes "prep for", "meeting with [entity]", "outreach to [entity]" queries
- [ ] Entity attribute expansion — extract anchor entity → load stub attributes → expand query with industry/geography/personas
- [ ] Cross-entity expansion — traverse `entity_relationships` to surface related content (e.g., healthcare research for a healthcare client)
- [ ] Structured brief output — entity summary, ranked content with relevance reasoning, knowledge gap report

**Gap detection:**
- [ ] Three gap types: `no_coverage` (0 results), `stale` (results >90 days old), `sparse` (1–2 results)
- [ ] Integrated into `mnemosyne prep` output

**New CLI command:**
```bash
mnemosyne prep "quarterly governance with [client]"
mnemosyne prep "outreach to [contact]"
```

**Good first issues for contributors:** entity mining script, stub quality validator, CONTEXTUAL_PREP intent classifier.

---

## v0.9.0 — Contradiction detection

- [ ] On new memory write: compare against existing knowledge for direct contradictions
- [ ] Flag conflicts rather than silently overwriting — human-agent teams need to know when the knowledge base disagrees with itself
- [ ] CLI: `mnemosyne contradict check "<new content>"` → reports conflicting documents + confidence

---

## v1.0.0 — Local model support + stable public API

**Local embedding (removes Azure OpenAI as hard dependency):**
- [ ] Embedding provider abstraction layer — `EmbedProvider` protocol, Azure and local as implementations
- [ ] Ollama adapter — `nomic-embed-text`, `mxbai-embed-large` tested at 1536-dim parity
- [ ] Sentence-transformers adapter — for environments where Ollama isn't available
- [ ] Provider selection in `env.example` — `MNEMOSYNE_EMBED_PROVIDER=azure|ollama|sentence-transformers`

**Stable public API:**
- [ ] REST/HTTP server mode — expose search, entity, and briefing as local endpoints (FastAPI, no external deps)
- [ ] Stable CLI contract — versioned with deprecation warnings before removal
- [ ] Multi-source support — plain markdown directories alongside QMD-indexed vaults
- [ ] Docker image — `ghcr.io/quanyeomans/agentic-context-mesh`

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
- Open a Discussion in [Roadmap & Priorities](https://github.com/quanyeomans/agentic-context-mesh/discussions) with a concrete use case
- Submit a PR — working code with tests moves faster than feature requests
- Share benchmark results from your own deployment — real-world data changes priorities

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, testing standards, and PR process.
The issues labelled [`good first issue`](https://github.com/quanyeomans/agentic-context-mesh/issues?q=label%3A%22good+first+issue%22) are explicitly scoped for new contributors.
