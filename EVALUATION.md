# Evaluation

Benchmark results for Mnemosyne hybrid search across phases of development.

---

## Current Results — v0.7.0 (R4 Hybrid Baseline)

**Suite:** 83 curated real-world cases across 6 query categories. Scored with strict NDCG@10 using graded relevance (0/1/2) with LLM-as-judge (GPT-4o-mini). Evaluated on a real-world personal knowledge base (~2,800 documents, 10,986 vectors at 1536-dim).

| Category | NDCG@10 | Cases | Notes |
|---|---|---|---|
| entity | 0.735 | 14 | Entity graph + alias resolution working well |
| keyword | 0.649 | 8 | BM25 baseline solid |
| procedural | 0.569 | 30 | Path-weighted re-rank shipped (Phase 8-A) |
| semantic | 0.553 | 13 | Hybrid vector load |
| multi_hop | 0.547 | 10 | QueryPlanner functional |
| temporal | 0.366 | 8 | Date-aware routing; improvement targeted in v0.8 |
| **Overall** | **0.580** | **83** | Curated suite, strict NDCG@10 |

**Hit@5: 0.8554** — a relevant document in the top 5 for 85.5% of queries.
**MRR@10: 0.7068**

All queries: `vec_failed=False` — hybrid search (BM25 + 1536-dim vector) operating correctly.

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
| **R4 hybrid baseline** | **0.580** | **83** | Curated suite only (session_log cases excluded); strict graded gold |

**Note on R4 vs R1:** R4 (0.580) and R1 (0.776) are not directly comparable. R1 used a mixed suite (263 cases, majority session_log with self-referential gold). R4 is curated-only (83 cases, independently graded gold), which is a stricter and more meaningful quality signal. The curated subset of R1 scored 0.595; R4 (0.580) is consistent with that baseline.

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

### Temporal (0.366 — weakest category)

Date-anchored queries are the current weakest point. Date-aware chunking and a timeline index shipped in Phase 4 and improved routing, but the category ceiling requires:

- Recency decay in ranking (recent documents scored higher for queries referencing "last week", "recently")
- Explicit `valid_from`/`valid_to` temporal attributes on entity relationships
- Structured date extraction from frontmatter and filenames as a first-class index

Targeted improvement is on the v0.8.0 roadmap via the `CONTEXTUAL_PREP` retrieval intent.

### Procedural (0.569 — at target)

Phase 8-A shipped path-weighted re-ranking for procedural queries (how-to, runbook, step-by-step patterns). This raised procedural NDCG from 0.390 (R1 post-refactor) to 0.569 (R4), meeting the ≥ 0.55 Phase 8 target.

### Multi-hop (0.547 — functional)

The `QueryPlanner` decomposes complex queries into sub-queries and runs them in parallel. Entity graph context injection (O-1) improved multi_hop NDCG by +0.035. The main remaining ceiling is the entity graph quality — a sparse or noisy entity set limits planner effectiveness.

### Entity (0.735 — strongest)

The entity graph with alias resolution is the system's strongest capability. Queries that name a known entity surface the entity stub plus related documents via relationship traversal.

---

## Data Residency

- **Vault content** is sent to Azure OpenAI for embedding (`text-embedding-3-large`) and synthesis (`gpt-4o-mini`). No data is retained by Azure beyond the API request.
- **All vectors** are stored in QMD's SQLite database on your own infrastructure.
- **Entity data** is stored in `entities.db` on your own infrastructure.

No vault content is transmitted to any third party other than Azure OpenAI for the operations listed above.

See [SECURITY.md](SECURITY.md) for full data handling detail.
