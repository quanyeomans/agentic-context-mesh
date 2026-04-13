# Evaluation

Benchmark results for Mnemosyne hybrid search across phases of development.

---

## Current Results — v0.8.1 (R13, 2026-04-13)

**Suite:** 95 curated real-world cases across 6 query categories. Scored with NDCG@10 using graded relevance (0/1/2). Evaluated on a real-world personal knowledge base (~2,800 documents, 11,316 vectors at 1536-dim).

| Category | NDCG@10 | Cases | Notes |
|---|---|---|---|
| entity | 0.811 | 14 | Entity graph + alias resolution; Neo4j vault crawler; refreshed gold |
| keyword | 0.599 | 8 | Full BM25+vector hybrid (v0.8.1 fix, was 0.488) |
| multi_hop | 0.536 | 10 | QueryPlanner RRF merge |
| procedural | 0.588 | 30 | Path-weighted re-rank |
| semantic | 0.504 | 13 | Hybrid vector |
| temporal | 0.577 | 20 | Date-aware retrieval; gold refreshed post-vault-crawl |
| **Overall** | **0.603** | **95** | Curated suite, strict NDCG@10 |

**Hit@5: 0.821** · **MRR@10: 0.669**

---

## Score Trajectory

### Phase 0–4 (weighted total — synthetic suite)

Early phases used a synthetic 43-case suite scored by category-weighted total rather than NDCG@10. These scores are not directly comparable to v2 instrument scores.

| Run | Score | Notes |
|---|---|---|
| BM25 baseline | 0.389 | Phase 0, 39-query suite |
| Azure vector only | 0.384 | Vector worse than BM25 on procedural/conceptual |
| Hybrid Phase 1 | 0.558 | RRF fusion; entity stubs sparse |
| Hybrid Phase 2.5 | 0.655 | Gold path fix + stub enrichment; Phase 1 gate ✅ |
| Hybrid Phase 3 | 0.762 | Briefing + classification; Phase 3 gate ✅ |
| Hybrid Phase 4 | 0.666 | Chunking + multi-hop planner; Phase 4 gate ✅ |

### v2 Instrument — NDCG@10 (real-world suite)

Starting Phase 5, the instrument switched to NDCG@10 with graded relevance on real-world cases derived from actual agent session logs. This is the authoritative metric going forward.

| Run | NDCG@10 | Cases | Notes |
|---|---|---|---|
| Phase 5 baseline | 0.320 | 134 | First real-world suite; temporal/multi_hop gold incomplete |
| Phase 6 final | 0.289 | 134 | temporal+multi_hop gold added; vector drift artifact |
| Phase 7-A recalibrated | 0.769 | 252 | Full gold rebuild at 768-dim; true baseline |
| Phase 7-B 1536-dim | 0.755 | 252 | 1536-dim reindex + gold rebuild; dimension kept |
| O-1 entity graph + planner | 0.754 | 245 | multi_hop +0.035 from QueryPlanner context injection |
| R1 post-refactor | 0.776 | 263 | Full gold rebuild after vault refactor |
| Phase 8-A procedural boost | 0.569 procedural | — | Path-weighted re-rank; target ≥ 0.55 met |
| R4 hybrid baseline | 0.580 | 83 | Curated suite only (session_log cases excluded); strict graded gold |
| R6 Sprint 4 | 0.564 | 95 | 95-case suite; entity enrichment + S1-C/CP; temporal 0.509 |
| R8 post-reembed | 0.538 | 95 | Force re-embed activated chunk_date filter; temporal regression (TMP-7) |
| R9 TMP-7 fix | 0.545 | 95 | vec K×4 when date filter active; temporal 0.423 |
| R10 Sprint 5 | 0.569 | 95 | Scorer path suffix fix + entity enrichment; temporal 0.535 |
| **R13 v0.8.1** | **0.603** | **95** | **Keyword hybrid fix + vault crawler + gold refresh; entity 0.811** |

**Note on R4 vs R1:** R4 (0.580) and R1 (0.776) are not directly comparable. R1 used a mixed suite (263 cases, majority session_log with self-referential gold). R4 is curated-only (83 cases, independently graded gold), which is a stricter and more meaningful quality signal.

**Note on R9 vs R10:** The temporal improvement (0.423 → 0.535) was largely a measurement artefact: the NDCG scorer was comparing collection-relative gold paths against absolute `/data/workspaces/` retrieved paths as exact strings (no match), assigning zero relevance to correctly retrieved documents. Path suffix matching resolves this. True retrieval capability did not change between R9 and R10 for temporal. Entity enrichment (76 entities now have vault_path + summaries) accounts for marginal non-temporal gains.

---

## Benchmark Methodology

### Suite format

Cases are defined in YAML suites under `suites/`. Each case specifies:

```yaml
id: R-CV-E01
category: entity          # entity / keyword / multi_hop / semantic / procedural / temporal
query: "Alice Chen contact details"
gold_paths:
  - path/to/document.md   # collection-relative path, matched against search results
  grade: 2                # 0 = irrelevant, 1 = partially relevant, 2 = highly relevant
```

Gold paths use collection-relative format matching `mnemosyne search` output — collection prefixes are stripped (e.g. `vault-agent-knowledge|shared/rules.md` → `shared/rules.md`).

### Graded relevance (v2 suite)

The v2 suite uses graded relevance (0/1/2) rather than binary gold matching:

| Grade | Meaning |
|---|---|
| 2 | Highly relevant — directly answers the query |
| 1 | Partially relevant — provides useful context but not the primary answer |
| 0 | Irrelevant — not expected in results |

NDCG@10 is computed with the standard DCG formula using these grades. LLM-as-judge (GPT-4o-mini) assigns grades for cases where relevance is not deterministic.

### Gold suite maintenance

Gold paths require maintenance when the underlying knowledge base is reorganised. The key risks:

- **Path moves**: files moved between directories change their collection-relative path
- **File deletion**: gold paths referencing deleted files produce zero-score on those cases
- **Vector drift**: re-embedding at different dimensions or with updated models changes ranking order, invalidating gold calibrated for the prior model

Best practice: after major vault reorganisations, run the gold validation script to check what percentage of gold paths are still present in the QMD index. Cases with missing gold paths should be updated or excluded — they produce artificially low NDCG scores and obscure genuine retrieval quality.

### Scoring interpretation

| NDCG@10 | Label |
|---|---|
| ≥ 0.78 | Epic 1 target |
| 0.70–0.78 | Strong — production quality |
| 0.60–0.70 | Solid — above typical RAG baseline |
| 0.55–0.60 | Current (v0.7.0 curated) |
| < 0.40 | Below BM25 baseline — instrument or system issue |

Production RAG systems on heterogeneous personal knowledge typically score 0.60–0.75 on held-out curated suites.

---

## Category Analysis

### Entity (0.811 — strongest)

The entity graph with alias resolution is the system's strongest capability. Queries that name a known entity surface the entity stub plus related documents via relationship traversal. The vault crawler (ADR-014) populates Neo4j with organisation and person nodes derived from vault structure, providing rich relationship context.

### Keyword (0.599 — improved in v0.8.1)

The v0.8.1 keyword hybrid fix removed `skip_vector = intent in (KEYWORD,)` which caused keyword queries to run BM25-only. All intents now run BM25 + vector in parallel via RRF. NDCG improved from 0.488 → 0.599 (+0.110).

### Temporal (0.577 — improved)

Date-aware chunking and a timeline index route temporal queries to date-filtered document sets. Gold suite maintenance (refreshing date-sensitive gold paths after vault reorganisation) resolved measurement artefacts that suppressed the reported score. The main ceiling for further improvement is recency decay in ranking.

### Procedural (0.588 — at target)

Path-weighted re-ranking for procedural queries (how-to, runbook, step-by-step patterns) meets the ≥ 0.55 target.

### Multi-hop (0.536 — functional)

The `QueryPlanner` decomposes complex queries into sub-queries and runs them in parallel. The main remaining ceiling is entity graph completeness — a sparse entity set limits planner effectiveness.

---

## Data Residency

- **Vault content** is sent to Azure OpenAI for embedding (`text-embedding-3-large`) and synthesis (`gpt-4o-mini`). No data is retained by Azure beyond the API request.
- **All vectors** are stored in QMD's SQLite database on your own infrastructure.
- **Entity data** is stored in `entities.db` on your own infrastructure.

No vault content is transmitted to any third party other than Azure OpenAI for the operations listed above.

See [SECURITY.md](SECURITY.md) for full data handling detail.
