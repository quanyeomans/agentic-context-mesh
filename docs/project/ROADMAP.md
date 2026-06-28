# Roadmap

Kairix is a contextual retrieval platform for human-agent teams that keeps your knowledge private and on your own infrastructure. This document describes the current state and near-term direction.

The canonical, live plan is the prioritised roadmap tracked in Linear (Core Platform); this document is its public summary. To request or influence priorities, open a [GitHub Discussion → Roadmap & Priorities](https://github.com/three-cubes/kairix/discussions) or a [GitHub Issue](https://github.com/three-cubes/kairix/issues) with a concrete use case — those are the inbound channels, not the canonical plan.

---

## Why this exists

The dominant pattern for AI agent memory is to send your knowledge to a third-party LLM service. This creates three problems:

1. **Privacy** — your organisation's knowledge, decisions, and relationships leave your infrastructure
2. **Context quality** — generic retrieval without entity awareness, temporal reasoning, or team-specific patterns produces mediocre results
3. **Team coherence** — agents and humans draw from different sources, breaking the shared context that makes teams effective

Kairix is the alternative: a private, on-infrastructure retrieval layer that both human team members and AI agents query against the same indexed knowledge base. Your data never leaves your servers.

---

## Current state — v2026.6.24.1 (released 2026-06-24)

**v2026.6.24 highlights:** the Linear connector ships (behind the `connector_linear` flag, default-off — pulls a Linear workspace's roadmap and docs into the knowledge store), and kairix now requires **Python 3.12 or newer** (recreate any host-venv / systemd environment on 3.12+ before upgrading; Docker images already ship 3.12).

**Standing retrieval baseline: weighted total 0.808 · NDCG@10 0.884 · Hit@5 0.913 · MRR@10 0.831**, measured on the production deployment against the 242-case `reflib` suite (recall / temporal / entity / conceptual / multi_hop / procedural categories). This is the v2026.6.9 measurement — the standing baseline carried forward, as no newer full sweep has been published. The Phase B expansion (#453) lifted the entity category from a 1-case measurement to 15 cases, so the entity-category NDCG of 0.800 is a meaningful figure rather than a single-question artefact.

The benchmark uses strict NDCG@10 scoring with graded relevance (0/1/2). See [EVALUATION.md](../evaluation/EVALUATION.md) for methodology and scoring interpretation.

**v2026.6.18 headline — "Set up kairix in your browser":**

1. **Browser setup wizard** — a guided in-browser flow walks a new operator from a fresh install to a working knowledge store: provider configuration, connector selection, and a first index, without hand-editing YAML.
2. **Sign-in for connectors** — OAuth sign-in for Slack, GitHub, and Google so operators connect a source by signing in rather than pasting API keys.
3. **In-product usage guide** — the agent usage guide ships inside the image and is reachable in-product, so agents get guidance instead of an error envelope (closes the production `UsageGuideNotFound` gap, #466).
4. **Agent memory write** — agents can persist a memory directly (`kairix remember` / `memory_write`), saving a dated markdown note that is indexed for BM25 retrieval immediately.

Earlier in this release line, the capability/skill recommender (`kairix recommend`) also shipped. See the [Recently shipped](#recently-shipped) section. (The `kairix mcp-calls` per-call *analytics* layer shipped; the full agent-feedback *eval loop* it was meant to feed — EPIC #465 — was retired NOT_PLANNED and re-derived as [#633](https://github.com/three-cubes/kairix/issues/633).)

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
| Automated eval suite generation | ✅ Shipped | `kairix eval generate/enrich` — GPL pipeline, graded relevance, LLM judge |
| YAML retrieval config | ✅ Shipped | `kairix.config.yaml` with validation; `RerankConfig`, `TemporalBoostConfig` |
| Automated benchmark regression gate | ✅ Shipped | CI gate on PRs via mock backend; blocks NDCG regression > 0.02 |
| Temporal chunk-date boost fixes | ✅ Shipped | Explicit-only guard, midpoint date, LIKE suffix path match (TMP-7B) |
| Cross-encoder re-ranking | ✅ Shipped | `ms-marco-MiniLM-L-6-v2`; opt-in via `rerank.enabled: true`; `pip install kairix-agentic-knowledge-mgt[rerank]` |
| OpenAI SDK embed client | ✅ Shipped | `OpenAIEmbedProvider` via `openai` SDK (#43) |
| Multi-collection support | ✅ Shipped | `hybrid_search()` accepts multiple collection names |
| Port auto-detection | ✅ Shipped | `kairix mcp serve` and `kairix setup` auto-select available port |
| Docker Compose primary deployment | ✅ Shipped | `docker compose up -d` as recommended install path |
| Entity-summary indexing | ✅ Shipped v2026.6.9 | ADR-036; flag `entity_summary_indexing_enabled` (default OFF until cutover) |
| Source-tier ranking | ✅ Shipped v2026.6.9 | `canonical_filename_allowlist` + `per_intent_overrides`; opt-in via config |
| Content-quality boost | ✅ Shipped v2026.6.9 | length / structure / recency signals; opt-in via config |
| Canonical entity seeding | ✅ Shipped v2026.6.9 | `canonical_entities:` YAML block + worker-boot seeder |
| MCP warm-state self-heal | ✅ Shipped v2026.6.9 | Detects in-process vs persisted state divergence + auto-recovers |
| Browser setup wizard | ✅ Shipped v2026.6.18 | Guided in-browser flow: provider config → connector selection → first index, no YAML hand-editing |
| Connector sign-in (OAuth) | ✅ Shipped v2026.6.18 | OAuth sign-in for Slack, GitHub, Google — connect a source by signing in, not pasting keys |
| In-product usage guide | ✅ Shipped v2026.6.18 | `kairix usage-guide` CLI + in-product agent guide shipped in the image (#466) |
| Agent memory write | ✅ Shipped v2026.6.18 | `kairix remember` / `memory_write` — dated markdown note, immediate BM25 index |
| Capability / skill recommender | ✅ Shipped | `kairix recommend` — ranks the kairix tool or local skill that fits a described task (PR #569/#570) |
| Per-call observability | ✅ Shipped | `kairix mcp-calls` — per-tool/agent latency + success/error analytics over `mcp_call_log` (the only shipped layer of EPIC #465) |
| Agent-feedback eval loop | 🔲 Planned | Failed queries → proposed eval cases → release-gate integration. The original EPIC #465 / Layers #538–#542 were retired NOT_PLANNED; re-derived as [#633](https://github.com/three-cubes/kairix/issues/633). The `kairix mcp-calls` analytics layer (above) shipped — the loop it feeds did not. |
| Incremental file watcher | 🔲 Planned | `watchfiles`-based daemon; sub-60s document store sync latency |
| Multi-user isolation | 🔲 Planned | Per-agent Neo4j namespace, collection-level access control |
| Streaming search response | 🔲 Planned | Server-sent events for MCP and REST consumers |
| Webhook / push indexing | 🔲 Planned | HTTP endpoint to trigger incremental embed on external write events |
| Local/offline embedding | ✅ Shipped | Ollama local embedding provider (select provider `ollama`); sentence-transformers adapter still planned |
| REST API server mode | 🔲 Planned | FastAPI server mode exposing search, entity, and briefing as HTTP endpoints |

**Benchmark category breakdown (242-case `reflib` suite, NDCG@10 — standing baseline, measured on the production VM 2026-06-09; no newer full sweep published):**

| Category | NDCG@10 | n | Weight | Notes |
|---|---|---|---|---|
| recall | 0.916 | 54 | 25% | Hybrid BM25 + vector |
| temporal | 0.558 | 20 | 20% | Date-aware chunking + chunk_date boost; under-performing relative to other categories |
| entity | 0.800 | 15 | 20% | Neo4j entity boost + alias resolution (entity-summary cutover gate ≥ 0.55 — passing) |
| conceptual | 0.917 | 75 | 15% | Semantic intent routing |
| multi_hop | 0.724 | 15 | 10% | QueryPlanner functional |
| procedural | 0.977 | 63 | 10% | Path-pattern re-rank active |
| **Overall** | **0.884** | 242 | — | Weighted total 0.808, Hit@5 0.913, MRR@10 0.831 |

---

## Tune retrieval to your knowledge — and why it matters

Different knowledge stores reward different retrieval strategies. A reference library full of curated technical docs reads differently from a stream of daily working notes, which reads differently from project plans or meeting transcripts. There is no single "best" search configuration that works equally well across all of them. A configuration that performs strongly on one corpus is often mediocre on another.

Kairix lets you tune retrieval per collection so each gets the strategy that suits its shape.

**The shipped reference library (≈6,000 open-source engineering docs) runs through a standard 200-query benchmark every release.** This is the single most useful number we publish: it shows what kairix actually delivers on a corpus you have access to and can re-run yourself.

This clean reference-library sweep is a separate upper-bound measurement — distinct from the 242-case production baseline above, not a re-measurement of it. Latest sweep (2026-05-08, post eval-pipeline standardisation, against the packaged `reflib-gold-v3.yaml` suite — suites ship inside the wheel under `kairix/data/suites/`):

| Strategy | What it does | NDCG@10 | Hit@5 | MRR@10 |
|---|---|---|---|---|
| **Hybrid (BM25 + vector, RRF k=20)** ★ | Keyword + meaning, fused by reciprocal rank | **0.949** | **0.965** | **0.896** |
| Hybrid (BM25 + vector, RRF k=40) | Same, broader candidate pool | 0.947 | 0.965 | 0.894 |
| Hybrid (BM25 + vector, RRF k=60) | Same, broadest pool | 0.947 | 0.965 | 0.894 |
| Hybrid (BM25 + vector, k=60, default boosts) | Hybrid + entity + procedural boosts | 0.945 | 0.965 | 0.892 |
| BM25-primary (vector backup, top-20) | Keywords first, vectors break ties | 0.725 | 0.845 | 0.675 |
| BM25-primary (top-10) | Same, smaller backup pool | 0.696 | 0.840 | 0.671 |
| BM25-primary (top-5) | Same, narrowest backup | 0.672 | 0.840 | 0.671 |
| BM25-only (keywords only) | No semantic understanding | 0.524 | 0.685 | 0.567 |

**Hit@5 = "the right document is in the top 5 results."** For a curated technical library, hybrid search puts the answer in the top 5 for **96.5% of queries** — vs **68.5%** for keyword-only.

The lift between BM25-only (0.524 NDCG) and hybrid-RRF (0.949 NDCG) is **+81%**. That's the whole game: keyword search alone misses anything that's worded differently from how it was indexed; semantic search alone misses exact-term matches; the two together cover both. RRF with a small k tightens the fusion further.

**Why your knowledge probably needs different settings**

The reference library is dense, technical, structured, with consistent terminology. Real working knowledge usually isn't:

- **Daily notes / journals** — date-shaped paths, conversational tone, reward temporal boosts (kairix `chunk_date_boost: enabled`).
- **People-heavy content** — meeting notes, CRM exports, performance reviews — reward entity boosts (`entity.factor`) so a query for "Acme Corp" surfaces the docs that genuinely mention them.
- **How-to and runbook content** — procedural-path boosts (`procedural.factor`) push tutorials and runbooks above general conceptual hits.
- **Long-form prose / research papers** — vector-primary fusion (`fusion_strategy: rrf`) outperforms keyword-primary because the wording is intentionally varied.
- **Wikis with strict naming conventions** — keyword-primary (`fusion_strategy: bm25_primary`) outperforms vector-primary because filename and heading matches carry signal.

**What you get from tuning**

- **Faster correct answers.** Your agent finds the right doc on the first attempt, not after three follow-up queries.
- **Less wasted context.** A higher Hit@5 means more of the top results are genuinely relevant — fewer adjacent-but-wrong docs eating context window.
- **Confident synthesis.** The agent's answers are grounded in the right sources, not in adjacent-topic ones it had to settle for.
- **Visible regressions.** Once you have a gold suite for your knowledge, every kairix upgrade runs the same benchmark and tells you concretely whether it got better or worse.

**How to tune for your own knowledge**

1. Build a small gold suite from your corpus — typically 30–50 questions with the documents you'd want returned. `kairix eval auto-gold` generates a starter suite from your indexed content.
2. Run a sweep against it: `kairix eval hybrid-sweep --suite your-gold.yaml --collection your-collection` (point `--suite` at your own gold file; the bundled reference suites ship inside the wheel under `kairix/data/suites/`). Takes ~10–15 minutes for 200 queries × 8 configs.
3. Look at the top config's `weighted_total` and `NDCG@10`. Set the matching `retrieval:` block on that collection in `kairix.config.yaml`.
4. Re-run periodically. The eval pipeline is standardised (shipped), so the same benchmark machinery, gold-builder, and judge are used consistently across the reference library, the bundled examples, and your own knowledge — meaning the numbers compare cleanly across releases and across corpora.

The reference-library numbers above are the upper bound for clean, well-curated content. Yours will likely be lower in absolute terms but the *shape* of the sweep (which strategy wins for which content) is the actionable signal.

---

## Recently shipped

Newest first. Connector and retrieval cutovers land behind default-off feature flags — reversible until each cutover soak validates parity. See [`how-to-upgrade-kairix`](../operations/runbooks/how-to-upgrade-kairix.md) for the operator recipe.

- **v2026.6.18 — "Set up kairix in your browser."** A guided browser setup wizard takes a new operator from a fresh install to a working knowledge store; OAuth sign-in for Slack, GitHub, and Google replaces pasting API keys; the agent usage guide ships inside the image and is reachable in-product (closes #466); and agents can persist a memory directly via `kairix remember` / `memory_write` (dated markdown note, immediate BM25 index). [Release notes](https://github.com/three-cubes/kairix/releases/tag/v2026.6.18).
- **Per-call observability (`kairix mcp-calls`).** Every MCP tool call is logged to `mcp_call_log` (tool, agent, latency, success/error) and surfaced via `kairix mcp-calls` for per-tool latency + failure inspection. This is the observability *foundation* only — the full agent-feedback *eval loop* meant to sit on top of it (call-log enrichment → auto-proposed eval cases → release-gate → dashboard) was scoped as EPIC [#465](https://github.com/three-cubes/kairix/issues/465) / Layers #538–#542 but retired NOT_PLANNED, and is re-derived as [#633](https://github.com/three-cubes/kairix/issues/633).
- **Capability / skill recommender** (`kairix recommend`, PR #569/#570) — give it a plain-English task and it ranks the kairix tool or local skill that fits, so agents reach for the right surface first.
- **CLI / MCP feature parity ([#168](https://github.com/three-cubes/kairix/issues/168)).** Every operation is exposed via both the CLI and the MCP server with uniform UX — shared use cases in `kairix/use_cases/`, with CLI and MCP as thin adapters over the same parameter names, defaults, output shapes, and help text.
- **v2026.6.9 — Retrieval quality v2 + entity-summary indexing.** The ADR-036 entity-summary indexing surface (Slices A–D: Protocol + projector + worker dispatch + operator surface), source-tier ranking with canonical-filename allowlist + per-intent overrides (#432), content-quality boost knobs (#458), fact-layer score floor + cross-layer dedup (#455), canonical entity seeding at worker boot (#431), MCP warm-state self-heal (#425), CLI search readability + snippet-width tuning (#385), prep envelope consistency (#433), contradict scoring honesty (#434), 42-case Phase B benchmark expansion (#453). [Release notes](https://github.com/three-cubes/kairix/releases/tag/v2026.6.9).
- **Topology v2 operator-config surface** — declare connectors / credentials / cc_pairs / collections / scope profiles / skills in `kairix.config.yaml` instead of code. Gated by `topology_v2_config`.
- **SharePoint, Slack, GitHub, and Notion connectors** — each shipped behind its own `connector_<name>` flag with a matching `topology_v2_<name>` per-source-unit Container pilot (per-drive, per-channel, per-repo, per-page-tree).
- **Linear connector** — pulls a Linear workspace's roadmap and docs (initiatives, projects, issues, documents, and project updates) into the knowledge store. Shipped behind its own `connector_linear` flag (default off) — add a Linear API key to your secret store, declare the connector in `kairix.config.yaml`, and turn it on when you're ready. See [`how-to-set-up-the-linear-connector`](../operations/runbooks/how-to-set-up-the-linear-connector.md).
- **Background maintenance loop** — `maintenance_loop` flag enables periodic orphan-vector cleanup with a 7-day soft-delete retention window. Replaces the reactive preflight orphan check.
- **Configurable agent-knowledge layout** — `paths.agent_knowledge_dir` + `paths.agent_memory_glob` let operators tell the `agent_knowledge_populated` onboard check where their memory files live and what shape they take.
- **Streaming bronze** — fetched bytes are extracted in-memory and discarded; `bronze_records` holds metadata only. Disk usage drops ~6000× vs the v2026.5.18 on-disk model. Recovery uses `connector.fetch(item_id)` to re-pull on demand.

**SharePoint per-drive path filtering.** Operators can now scope a SharePoint drive by include / exclude paths instead of indexing the whole drive (shipped v2026.5.24a4). See [`docs/architecture/sharepoint-path-filtering.md`](../architecture/sharepoint-path-filtering.md).

The connector framework continues to evolve — guided per-connector configuration (`discover` / `configure` / `status` with pre-ingest volumetric estimates and during-ingest progress), config-driven chunker selection, and the introduce → cutover → retire soak for each `topology_v2_<name>` flag. The live, detailed sequencing for connector work is tracked in Linear (Core Platform) and on GitHub; see [`docs/architecture/connector-ingestion-architecture.md`](../architecture/connector-ingestion-architecture.md) and [`docs/architecture/guided-configuration.md`](../architecture/guided-configuration.md) for the design.

---

## Near-term

The live, prioritised plan is tracked in Linear (Core Platform) and on GitHub Issues. The items below are the public summary of what's next; for the detailed sequencing follow the linked issues.

- **Latency tail investigation** ([#436](https://github.com/three-cubes/kairix/issues/436)) — post-warm search p95 (~640ms) exceeds the 500ms benchmark threshold. Investigate the BM25 / vector / rerank dispatch hot path. The reliability story is solid; latency is the next blocker for "kairix first, every task" adoption.
- **Canonical entity consistency for self** ([#467](https://github.com/three-cubes/kairix/issues/467)) — `facts_about('Kairix')` returns nothing despite `entity_summary_indexing_enabled=true`. Ship a default `canonical_entities:` seed that includes Kairix itself, or have `facts_about` fall back to entity-summaries when no canonical match exists.
- **Contradict tool category refinement** ([#468](https://github.com/three-cubes/kairix/issues/468)) — separate `contradiction` / `unsupported` / `not found` categories with per-category confidence, so the overstatement signal stops firing on the mere absence of evidence. Refines closed #434.
- **Configurable default search scope** — operators control which collections participate in default search scopes from `kairix.config.yaml` via a per-collection `in_default: bool` flag, replacing the hardcoded `_RESERVED_COLLECTIONS = {"reference-library"}` carve-out. Includes a `DefaultCollectionResolver` refactor that consolidates today's 5-branch elif chain into a `match` dispatch with predicates owned by `CollectionsConfig` / `AgentDef`. Composable named scopes (`scopes.research`, `scopes.with-history`) and per-collection `retrieval:` overrides are design-tracked. See [configurable-default-scope.md](../architecture/configurable-default-scope.md).
- **Pre-release container registry** — CI builds and pushes alpha Docker images to GHCR on green `main` builds (`ghcr.io/three-cubes/kairix:2026.5.1a1`). VM deploys via `docker compose pull` instead of building from source. Tests the exact same image end users will get. Stable tags pushed on merge to `main`.
- **File watcher** — `watchfiles`-based daemon replacing the 60-second embed cron. Document store changes embedded within seconds of write, reducing lag for session-prep queries against recently-added content.
- **Structured-log observability** — structured JSON log output (`LOG_LEVEL=json`) parsed by a lightweight dashboard for per-query latency, intent distribution, entity hit rate, and RRF score distributions without third-party tooling. Complements the shipped `kairix mcp-calls` per-call analytics over `mcp_call_log`.
- **sentence-transformers embedding adapter** — adds a sentence-transformers local embedding adapter alongside the shipped Ollama provider (removes Azure OpenAI as a hard dependency for non-Azure deployments).
- **REST API** — FastAPI server mode exposing search, entity, and briefing as local HTTP endpoints.

---

## How priorities are set

The project is maintained by [@quanyeomans](https://github.com/quanyeomans). Priorities are influenced by:

1. **Benchmark gaps** — categories with low NDCG@10 score get attention first
2. **Deployment blockers** — things that prevent new operators from adopting the system
3. **Community demand** — upvoted Discussions and well-articulated issues

If you want to influence the roadmap, the most effective approaches are:
- Open a Discussion in [Roadmap & Priorities](https://github.com/three-cubes/kairix/discussions) with a concrete use case
- Submit a PR — working code with tests moves faster than feature requests
- Share benchmark results from your own deployment — real-world data changes priorities

---

## Contributing

See [CONTRIBUTING.md](../CONTRIBUTING.md) for setup, testing standards, and PR process.
The issues labelled [`good first issue`](https://github.com/three-cubes/kairix/issues?q=label%3A%22good+first+issue%22) are explicitly scoped for new contributors.
