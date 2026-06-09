# Roadmap

Kairix is a contextual retrieval platform for human-agent teams that keeps your knowledge private and on your own infrastructure. This document describes the current state and near-term direction.

Discussion of priorities and feature direction happens in [GitHub Discussions → Roadmap & Priorities](https://github.com/three-cubes/kairix/discussions).

---

## Why this exists

The dominant pattern for AI agent memory is to send your knowledge to a third-party LLM service. This creates three problems:

1. **Privacy** — your organisation's knowledge, decisions, and relationships leave your infrastructure
2. **Context quality** — generic retrieval without entity awareness, temporal reasoning, or team-specific patterns produces mediocre results
3. **Team coherence** — agents and humans draw from different sources, breaking the shared context that makes teams effective

Kairix is the alternative: a private, on-infrastructure retrieval layer that both human team members and AI agents query against the same indexed knowledge base. Your data never leaves your servers.

---

## Current state — v2026.6.9 (released 2026-06-09)

**Weighted total 0.808 · NDCG@10 0.884 · Hit@5 0.913** measured on the production deployment against the 242-case `reflib` suite (entity / temporal / multi_hop / conceptual / procedural / recall categories). The Phase B expansion shipped in this release (#453) lifted the entity category from a 1-case measurement to 15 cases — the headline entity-category NDCG of 0.800 is now a meaningful figure rather than a single-question artefact.

The benchmark uses strict NDCG@10 scoring with graded relevance (0/1/2). See [EVALUATION.md](EVALUATION.md) for methodology and scoring interpretation.

**This release's headline themes:**

1. **Entity-aware retrieval surface** (ADR-036) — synthetic `entity-summaries` collection that projects Neo4j entity descriptions into first-pass BM25 + vector search. Default OFF; activates via `entity_summary_indexing_enabled: true` per the [cutover runbook](../operations/runbooks/entity-summary-cutover.md). Production VM cutover landed 2026-06-09; soak-validation in progress.
2. **Retrieval-quality knobs end-to-end** — source-tier ranking (#432), canonical-filename allowlist + per-intent overrides, content-quality boost (#458), fact-layer floor + cross-layer dedup (#455), canonical entity seeding (#431).
3. **Operator confidence wave** — MCP warm-state self-heal (#425), prep envelope consistency (#433), contradict scoring honesty (#434), CLI snippet-width control (#385).
4. **Benchmark realism** — 42 new operator-curated entity/temporal/multi-hop queries grounded in shipped reference content (#453).

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
| Incremental file watcher | 🔲 Planned | `watchfiles`-based daemon; sub-60s document store sync latency |
| Observability dashboard | 🟡 In design ([#464](https://github.com/three-cubes/kairix/issues/464) Layer 5) | Per-tool/agent/intent latency + failure analytics over `mcp_call_log` |
| Agent-feedback eval loop | 🟡 In design ([#464](https://github.com/three-cubes/kairix/issues/464)) | Real failed queries → proposed eval cases → release-gate integration |
| Multi-user isolation | 🔲 Planned | Per-agent Neo4j namespace, collection-level access control |
| Streaming search response | 🔲 Planned | Server-sent events for MCP and REST consumers |
| Webhook / push indexing | 🔲 Planned | HTTP endpoint to trigger incremental embed on external write events |
| Local/offline embedding | 🔲 Planned | `EmbedProvider` abstraction; Ollama + sentence-transformers adapters |
| REST API server mode | 🔲 Planned | FastAPI server mode exposing search, entity, and briefing as HTTP endpoints |

**Benchmark category breakdown (242-case `reflib` suite, NDCG@10 — measured on production VM 2026-06-09, pre-flip baseline):**

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

Latest sweep (2026-05-08, post eval-pipeline standardisation, against `suites/reflib-gold-v3.yaml`):

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
2. Run a sweep against it: `kairix eval hybrid-sweep --suite suites/your-gold.yaml --collection your-collection`. Takes ~10–15 minutes for 200 queries × 8 configs.
3. Look at the top config's `weighted_total` and `NDCG@10`. Set the matching `retrieval:` block on that collection in `kairix.config.yaml`.
4. Re-run periodically. Recent kairix work standardised the eval pipeline (issue [#143](https://github.com/three-cubes/kairix/issues/143)) so the same benchmark machinery, gold-builder, and judge are now used consistently across the reference library, the bundled examples, and your own knowledge — meaning the numbers compare cleanly across releases and across corpora.

The reference-library numbers above are the upper bound for clean, well-curated content. Yours will likely be lower in absolute terms but the *shape* of the sweep (which strategy wins for which content) is the actionable signal.

---

## Recently shipped

Behind default-off feature flags — reversible until each cutover soak validates parity. See [`how-to-upgrade-kairix`](../operations/runbooks/how-to-upgrade-kairix.md) for the operator recipe.

- **v2026.6.9 — Retrieval quality v2 + entity-summary indexing.** The ADR-036 entity-summary indexing surface (Slices A–D: Protocol + projector + worker dispatch + operator surface), source-tier ranking with canonical-filename allowlist + per-intent overrides (#432), content-quality boost knobs (#458), fact-layer score floor + cross-layer dedup (#455), canonical entity seeding at worker boot (#431), MCP warm-state self-heal (#425), CLI search readability + snippet-width tuning (#385), prep envelope consistency (#433), contradict scoring honesty (#434), 42-case Phase B benchmark expansion (#453). [Release notes](https://github.com/three-cubes/kairix/releases/tag/v2026.6.9).
- **Topology v2 operator-config surface** — declare connectors / credentials / cc_pairs / collections / scope profiles / skills in `kairix.config.yaml` instead of code. Gated by `topology_v2_config`.
- **SharePoint, Slack, GitHub, and Notion connectors** — each shipped behind its own `connector_<name>` flag with a matching `topology_v2_<name>` per-source-unit Container pilot (per-drive, per-channel, per-repo, per-page-tree).
- **Background maintenance loop** — `maintenance_loop` flag enables periodic orphan-vector cleanup with a 7-day soft-delete retention window. Replaces the reactive preflight orphan check.
- **Configurable agent-knowledge layout** — `paths.agent_knowledge_dir` + `paths.agent_memory_glob` let operators tell the `agent_knowledge_populated` onboard check where their memory files live and what shape they take.
- **Streaming bronze** — fetched bytes are extracted in-memory and discarded; `bronze_records` holds metadata only. Disk usage drops ~6000× vs the v2026.5.18 on-disk model. Recovery uses `connector.fetch(item_id)` to re-pull on demand.

Next milestone in this thread is **Wave F (chunker plugins)** — each connector picks its chunker by config rather than baked-in choice. Plus the per-connector cutover soak that promotes each `topology_v2_<name>` flag from introduce → cutover → retire over the next several releases.

**KFEAT-022 — guided configuration for connectors.** Three new CLI subcommands per connector (`discover`, `configure`, `status`) so operators (and agents) pick what to index from a plain-English list instead of pasting raw IDs into YAML. Adds pre-ingest volumetric estimates (file count, disk impact, expected ingest time) so a 1 TB SharePoint pick surfaces its cost before the worker starts; adds during-ingest progress so the operator sees when kairix is fully operationalised on the new collection. SharePoint pilot lands first behind `guided_configuration_sharepoint`; Slack / GitHub / Notion inherit the pattern. See [`docs/architecture/guided-configuration.md`](../architecture/guided-configuration.md).

**SharePoint per-drive path filtering.** Operators can now scope a SharePoint drive by include / exclude paths instead of indexing the whole drive (shipped v2026.5.24a4). Prerequisite for KFEAT-022's discovery surface walking one level deeper than drives. See [`docs/architecture/sharepoint-path-filtering.md`](../architecture/sharepoint-path-filtering.md).

---

## Near-term

- **EPIC: agent-call instrumentation + eval-feedback loop** ([#464](https://github.com/three-cubes/kairix/issues/464)) — *triggered by 2026-06-09 v2026.6.9 dogfood feedback.* Five-layer build: (1) per-call classification + quality-signal capture into `mcp_call_log` (intent, agent_caller, result_shape, outcome_signal, optional agent-self-reported quality 1–5); (2) analytics CLI / MCP surface (`kairix mcp-calls analytics`, `kairix mcp-calls failing-queries`); (3) periodic eval-feedback loop that clusters failed queries → proposes new suite entries via `kairix eval generate --from-call-log`; (4) release-gate integration where auto-generated cases become per-release gates after operator review; (5) optional operator dashboard. The shape mirrors what's worked elsewhere in kairix — F49 mechanises test-discipline paydown, ADR-036 mechanises cutover, this EPIC mechanises retrieval-quality paydown. Real agent failures become release gates without manual curation. Foundation for the "agent-facing operating layer" Shape verdict identified as the next adoption unlock. **Top priority for next session.**
- **P0 — install the kairix usage guide in production images** ([#465](https://github.com/three-cubes/kairix/issues/465)) — `mcp-kairix__usage_guide` currently returns `UsageGuideNotFound` on production. The guide should ship in the image at build time, not require an operator `kairix onboard guide` step. Agents that call the guide get an error envelope instead of guidance, falling back to hand-written memory rules.
- **P1 — research MCP tool accepts agent-supplied evidence** ([#466](https://github.com/three-cubes/kairix/issues/466)) — `tool_research` today synthesises from vault only. Add an `additional_evidence: list[{source, content, retrieved_at}]` parameter so agents can hand external context (web fetches, current conversation transcript) to research for combined synthesis. Closest existing surface to the "Researcher Agent" pattern; needs this to be useful as the agent-adoption layer.
- **P2 — contradict envelope refinement** ([#467](https://github.com/three-cubes/kairix/issues/467)) — separate `contradicts` / `unsupported` / `not_found` categories with per-category confidence; `has_contradictions` only fires on active disagreement. Refines closed #434.
- **P2 — canonical entity consistency for self** ([#468](https://github.com/three-cubes/kairix/issues/468)) — `facts_about('Kairix')` returns nothing despite `entity_summary_indexing_enabled=true`. Ship a default `canonical_entities:` seed that includes Kairix itself, OR have `facts_about` fall back to entity-summaries when no canonical match.
- **P1 — latency tail investigation** ([#436](https://github.com/three-cubes/kairix/issues/436)) — production agent measured p95 ~10s at concurrency 2 (vs. 640ms baseline). Investigate vector / BM25 / rerank hotspots. The reliability story is solid now; performance is the next blocker for "Kairix first, every task" adoption.
- **CLI / MCP feature parity** ([#168](https://github.com/three-cubes/kairix/issues/168)) — every kairix feature exposed via both the CLI and the MCP server with uniform UX. One use case per operation in `kairix/use_cases/`; CLI and MCP become thin adapters with shared parameter names, defaults, output shapes, and help text. Closes the timeline code-path divergence ([#163](https://github.com/three-cubes/kairix/issues/163)) by collapsing it onto the same use case both surfaces consume; subsumes the MCP error envelope shape gap ([#165](https://github.com/three-cubes/kairix/issues/165)) by pushing error-envelope construction into the use case. 8 surface-parity gaps identified (entity suggest/validate missing on MCP; entity get missing on CLI; prep, research, brief, usage_guide each missing on one side). Phased: Phase 1 timeline (template + #163 fix), Phase 2 UX parity audit on already-converged tools, Phase 3 fill missing surfaces in operator-priority order, Phase 4 uniformity polish + help-text single source of truth. See [cli-mcp-feature-parity.md](../architecture/cli-mcp-feature-parity.md). **Targeted for next sprint.**
- **Eval-module rectification** ([#143](https://github.com/three-cubes/kairix/issues/143)) — fix accumulated structural debt across `kairix/quality/eval/`: 6 silent bugs (4 silently-skipped tests, procedural-pattern filter applied to titles, inverted credential-merge logic, DB-leak in gold_builder), 2 BLOCKER S2083 vulnerabilities + prompt-injection vectors, 11+ `*_fn=None` test-substitution kwargs (eliminated via `LLMJudge` / `QueryGenerator` / `Retriever` / `ChatBackend` protocols + fakes + DI), `_call_llm` private-symbol cross-module import, raw-SQL FTS access in gold_builder (moved onto `DocumentRepository`), missing BDD + integration coverage for the gold-builder / judge / generator pipelines. `generate.py` (876 lines) split into 3 modules. Phased: Phase 0 bug fixes, Phase 0b security pass, Phase 1 protocols + fakes (foundation), Phase 2a owner-led judge refactor, Phase 2b parallel agent fan-out (file-isolated: sweep, gold_builder, generate), Phase 3 cli.py integration + deprecated-kwarg removal, Phase 4 VM deploy, Phase 5 reference-library benchmark validation against v2026.4.27 baseline (R10 0.8171, NDCG@10 0.8385). See [eval-module-refactor.md](../architecture/eval-module-refactor.md).
- **Configurable default search scope** — operators control which collections participate in default search scopes from `kairix.config.yaml` via a per-collection `in_default: bool` flag, replacing the hardcoded `_RESERVED_COLLECTIONS = {"reference-library"}` carve-out. Includes a `DefaultCollectionResolver` refactor that consolidates today's 5-branch elif chain into a `match` dispatch with predicates owned by `CollectionsConfig` / `AgentDef` rather than the resolver. **Phase 2** (composable named scopes — `scopes.research`, `scopes.with-history`) is design-tracked but uncommitted; held until a second concrete use case lands. **Phase 3** lifts the parallel `reference-library` retrieval-config hardcode in `config_loader.py:384` into per-collection `retrieval:` overrides. See [configurable-default-scope.md](../architecture/configurable-default-scope.md).
- **Pre-release container registry** — CI builds and pushes alpha Docker images to GHCR on green `main` builds (`ghcr.io/three-cubes/kairix:2026.5.1a1`). VM deploys via `docker compose pull` instead of building from source. Tests the exact same image end users will get. Stable tags pushed on merge to `main`.
- **File watcher** — `watchfiles`-based daemon replacing the 60-second embed cron. Document store changes embedded within seconds of write, reducing lag for session-prep queries against recently-added content.
- **Observability dashboard** — structured JSON log output (`LOG_LEVEL=json`) parsed by a lightweight dashboard. Per-query latency, intent distribution, entity hit rate, RRF score distributions surfaced without third-party tooling.
- **Local/offline embedding** — `EmbedProvider` abstraction; Ollama and sentence-transformers adapters (removes Azure OpenAI as hard dependency for non-Azure deployments).
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
