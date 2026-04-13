# Kairix — Service Level Objectives

Defines the quality, performance, and reliability targets for the Kairix retrieval platform. These SLOs guide instrumentation priorities, gate production deployments, and measure the value delivered to the agents and humans using the system.

---

## 1. Retrieval Quality SLOs

Measured by the private v2-real-world benchmark suite (83 curated cases, strict NDCG@10 with graded relevance). Gates apply per-category — a single category regression blocks promotion.

| Category | Current (R10) | SLO Target | Hard Floor (blocks deploy) |
|---|---|---|---|
| entity | 0.751 | ≥ 0.75 | < 0.70 |
| procedural | 0.564 | ≥ 0.60 | < 0.55 |
| multi_hop | 0.549 | ≥ 0.65 | < 0.55 |
| semantic | 0.519 | ≥ 0.55 | < 0.50 |
| temporal | 0.535 | ≥ 0.55 | < 0.50 |
| keyword | 0.439 | ≥ 0.55 | < 0.45 |
| **overall NDCG@10** | **0.5686** | **≥ 0.60** | **< 0.55** |
| **Hit@5** | 0.874 | ≥ 0.88 | < 0.85 |
| **MRR@10** | 0.673 | ≥ 0.70 | < 0.65 |

**How to measure:** `kairix benchmark run --suite suites/v2-real-world.yaml` on the TC VM. Run after any change to search, entity, temporal, or embed modules. Results saved to `benchmark-results/`.

---

## 2. Latency SLOs

Measured from search() call to SearchResult returned, inclusive of BM25 + vector + RRF + entity boost.

| Operation | P50 target | P95 target | Current baseline |
|---|---|---|---|
| `kairix search` (hybrid) | < 300ms | < 1,200ms | ~234ms observed (single query) |
| `kairix brief` | < 8s | < 20s | Not systematically measured |
| `kairix vault crawl` (full) | < 60s | < 120s | Not yet measured |
| `kairix embed` (incremental, 0 changes) | < 5s | < 10s | Exits fast in practice |

**How to measure (not yet instrumented):** `SearchResult.latency_ms` is captured per query but not aggregated. To instrument:
```bash
kairix search "query" --json | jq .latency_ms
```
A latency log aggregator (see Roadmap v1.0.0) will track P50/P95 from `KAIRIX_SEARCH_LOG`.

---

## 3. Entity Graph Health SLOs

Measured by `kairix vault health --json` after each vault crawl.

| Metric | SLO Target | Current (pre-R11) |
|---|---|---|
| `vault_path` coverage (orgs) | 100% | 0% (entities.db) → TBD (Neo4j post-crawl) |
| `vault_path` coverage (persons) | 100% | 0% (entities.db) → TBD |
| `summary` coverage (orgs) | ≥ 80% | Not measured |
| `summary` coverage (persons) | ≥ 80% | Not measured |
| WORKS_AT edge density (persons with org) | ≥ 70% | Not measured |
| MENTIONS edge count | Growing | Not measured |

**How to measure:** `kairix vault health --json` after each `kairix vault crawl`. The `ok` property gates downstream enrichment.

---

## 4. Embed Pipeline SLOs

| Metric | SLO Target | How to check |
|---|---|---|
| Post-embed recall gate | ≥ 4/5 (80%) | Embed log: `grep "Recall:" embed.log` |
| Vector count (post-full-embed) | ≥ 5,000 | `SELECT COUNT(*) FROM vectors_vec` |
| Dimension consistency | 1536 everywhere | `SELECT length(embedding)/4 FROM vectors_vec LIMIT 1` |
| Hourly cron success rate | ≥ 95% over 7 days | `grep "Done —" embed.log | wc -l` / expected runs |

---

## 5. Operational SLOs

| Metric | SLO Target | How to check |
|---|---|---|
| Hourly embed cron — no silent failures | 0 consecutive failures | Log monitoring |
| Nightly entity cron — runs within 1h of schedule | ≥ 95% on-time | `tail entity-relation-seed.log` |
| `kairix vault health ok` | True (after crawl run) | `kairix vault health --json \| jq .ok` |
| CI test gate | All pass, coverage ≥ 80% | `pytest --cov-fail-under=80` |

---

## 6. User Experience Targets

These are qualitative targets that the SLOs above should collectively deliver. They are not directly measurable but inform what we instrument.

| UX Target | Underlying SLOs |
|---|---|
| **Agent arrives ready to work** — session briefing surfaces relevant entities, decisions, recent activity | Retrieval quality SLOs; Entity graph health SLOs |
| **Vault queries feel instant** — agent search completes before the user notices | Latency P50 < 300ms |
| **Entity lookups are reliable** — "tell me about Bupa" returns the correct org node with vault path | entity NDCG ≥ 0.75; vault_path coverage 100% |
| **Temporal queries work correctly** — "what happened last week" returns date-scoped results | temporal NDCG ≥ 0.55; TMP-2 date filter coverage |
| **New vault content is findable within 1 hour** — agent can query documents added today | Embed cron SLOs |
| **Knowledge degradation is visible** — stale entities, missing summaries, broken crons are surfaced | `kairix vault health ok`; `kairix curator health ok` |

---

## 7. Feedback Loop (not yet instrumented)

The SLOs above measure retrieval mechanics. The missing signal is **agent task success** — whether search results actually helped the agent complete its work.

**Proposed:** A lightweight signal where agents log a `search_outcome` event (useful / not_useful / no_results) back to `KAIRIX_SEARCH_LOG`. This would allow correlation between NDCG@10 and real-world utility, and identify queries that score well in benchmark but underperform in production.

See ROADMAP.md v1.1.0 — Agent Feedback Loop.

---

## 8. Benchmark Run Schedule

| Trigger | Suite | Purpose |
|---|---|---|
| Any PR touching search/entity/temporal/embed | `suites/example.yaml` (CI) | Regression gate |
| After vault crawl + entity graph population | `suites/v2-real-world.yaml` (VM) | R11 baseline |
| Monthly on VM | `suites/v2-real-world.yaml` | Drift detection |
| After any major feature ship | `suites/v2-real-world.yaml` | Version benchmark |

Current run: **R10** (2026-04-10). Next planned: **R11** (post-vault-crawler entity graph).
