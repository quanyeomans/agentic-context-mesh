# Evaluation

Kairix is evaluated against a curated real-world benchmark derived from actual agent session queries across your knowledge base. Scoring uses strict NDCG@10 with graded relevance (0 = irrelevant, 1 = partial, 2 = directly answers the query).

---

## Current Performance — v2026.4.27

**R10: 0.8171 · NDCG@10: 0.8385 · Hit@5: 0.9629 · MRR@10: 0.7614**

Evaluated on a 160-query reference library gold suite across six query types.

| Query type | NDCG@10 | What this means |
|---|---|---|
| Keyword / proper noun | **0.775** | Version strings, error codes, specific document names resolve accurately via hybrid BM25 + vector |
| Entity lookups | **0.8626** | Named entities (people, organisations, concepts) surface canonical stub plus related documents; see note below |
| Procedural queries | **0.8716** | How-to questions and runbook lookups return step-relevant documents ahead of tangentially related content |
| Temporal queries | **0.7930** | "What happened last week", "decisions in March" route to date-scoped results |
| Multi-hop queries | **0.721** | Questions spanning multiple entities or topics decompose into sub-queries and fuse results |
| Semantic queries | **0.842** | Abstract conceptual questions retrieve relevant documents without exact term overlap |

A relevant document appears in the top 5 results for **96% of queries**.

> **Entity NDCG note:** The entity score reflects knowledge base composition at the time of evaluation. Scores are expected to improve as the Neo4j entity graph densifies. Entity NDCG optimisation is on the roadmap.

## What good retrieval enables

The benchmark categories map directly to the use cases Kairix is built for:

**Entity-aware preparation** — "Tell me about Acme Corp" or "What has TechCorp been working on" returns the entity's curated stub, relationship context (who works there, what projects are active), and ranked documents. The entity score reflects this working reliably on real queries; score improvements are expected as the Neo4j graph densifies.

**Meeting and session prep** — temporal and multi-hop retrieval together cover queries like "what decisions were made last month about the platform architecture" or "what's the current status of the Azure connector and why was it chosen." These require date-scoped retrieval and multi-document reasoning — both categories score above 0.75.

**Procedural knowledge** — agents querying runbooks, standards, and how-to guides get step-relevant content ranked above generic background material. The 0.872 procedural score reflects path-weighted re-ranking working as intended.

**Keyword accuracy** — error codes, version strings, file paths, and proper nouns return precise results. The hybrid search design (BM25 + vector in parallel for all intents) delivers keyword NDCG of 0.848.

---

## Methodology

### Document identity

Relevance judgments follow the TREC qrels convention: gold documents are identified by their stable note title (the filename stem), not by filesystem path. This is the same stable identifier that Obsidian wikilinks use — `[[Acme Corp]]` links to `Acme Corp.md` regardless of which folder the file sits in.

A retrieved document is considered a match when its filename stem normalises to the gold title. The normalisation is deterministic: lowercase, with spaces, underscores, and hyphens consolidated into a single hyphen. This means:

- `02-Areas/00-Clients/Acme-Corp/Acme-Corp.md` matches gold title `Acme Corp`
- `Archive/acme-corp.md` matches the same gold title
- Moving or reorganising a note does not affect its benchmark score

This design means the benchmark measures retrieval quality rather than path accuracy, and suite files remain valid as the vault evolves.

### Suite format

Cases are defined in `suites/example.yaml`. Each specifies a query, the expected relevant documents by title, and a graded relevance score:

```yaml
- id: E-01
  category: entity
  query: "Jordan Blake role and responsibilities"
  score_method: ndcg
  gold_titles:
    - title: jordan-blake
      relevance: 2
    - title: team-overview
      relevance: 1
```

Relevance scale: **2** = directly answers the query; **1** = partially relevant; **0** = not relevant (implicit for any document not listed).

Suites that pre-date the title-based format use `gold_paths` (filesystem paths) and continue to work — path matching is retained as a fallback so existing suites do not need to be rewritten.

### Metrics

**NDCG@10** (primary) rewards retrieving highly relevant documents at top positions and penalises ranking partially relevant documents above highly relevant ones. A perfect score of 1.0 means every relevant document appeared at the highest possible position, in relevance order.

**Hit@5** — the fraction of queries where at least one relevant document (relevance ≥ 1) appears in the top 5 results. Measures broad coverage independent of ranking order.

**MRR@10** (Mean Reciprocal Rank) — the average of 1/rank of the first relevant document across queries. Measures how quickly the system surfaces any relevant result.

| NDCG@10 | Interpretation |
|---|---|
| ≥ 0.80 | Strong — entity-quality retrieval |
| 0.70–0.80 | Solid — above typical RAG baseline for heterogeneous private knowledge |
| 0.55–0.70 | Functional — relevant content found but ranking has room to improve |
| < 0.50 | Needs attention — comparable to BM25-only baseline |

### Running the benchmark

```bash
kairix benchmark run --suite suites/example.yaml
```

The CLI output reports both the **weighted total** (category-weighted average used for phase gates) and **NDCG@10** (the standard IR metric, computed per-case and averaged across all `ndcg`-scored cases):

```
============================================================
BENCHMARK RESULTS
============================================================
Weighted total: 0.8171  [Strong]
NDCG@10:       0.8385  (Hit@5: 0.9629  MRR@10: 0.7614)

Category breakdown:
  temporal     0.7930  (weight 10%, n=8)  ...
  entity       0.8626  (weight 20%, n=12) ...
  ...

Per source type:
  markdown    NDCG@10=0.850  MRR@10=0.830  Hit@10=0.920  (queries=42)
  pptx        NDCG@10=0.710  MRR@10=0.680  Hit@10=0.810  (queries=18)
  pdf         NDCG@10=0.740  MRR@10=0.710  Hit@10=0.850  (queries=22)
  docx        NDCG@10=0.790  MRR@10=0.760  Hit@10=0.880  (queries=15)
  xlsx        NDCG@10=0.620  MRR@10=0.580  Hit@10=0.730  (queries=12)
  email       NDCG@10=0.770  MRR@10=0.740  Hit@10=0.860  (queries=30)
  calendar    NDCG@10=0.810  MRR@10=0.790  Hit@10=0.880  (queries=20)

Boundary-spanning canaries: 12/12 passed (100%)
  slide     3/3 passed
  row       3/3 passed
  event     3/3 passed
  message   3/3 passed
```

NDCG@10 is the number to report and track across releases. The weighted total drives phase gate pass/fail.

The public example suite (`suites/example.yaml`) contains anonymised, domain-neutral cases. The real-world scores above were measured against a private vault-specific suite that cannot be published.

---

## Per-source-type evaluation (ADR-028 scaffolding)

The eval harness reports **three measurement surfaces** that ADR-028 needs to evaluate per-type chunker plugins. None of them change the existing overall NDCG@10 number — they layer additional detail on top.

### Per-source-type Recall@k slicing

`BenchmarkResult.summary["per_source_type"]` carries one row per source type with NDCG@10, MRR@10, Hit@10, and query count. Source type is derived from each gold answer's title or path extension, with an explicit `source_type:` field on the suite case as the override.

A query asking about a `.pptx` deck contributes to the `pptx` slice; a query asking about a `.md` note contributes to `markdown`. Catastrophic failure on one type (a chunker that breaks XLSX rows) shows up as a slice that lags well below the overall NDCG@10 — invisible in the global average.

### Boundary-spanning canary suite

`suites/per-type-canary-suite.yaml` ships 12 canary queries — three per atomic unit type (slide, row, event, message). Each canary's gold answer deliberately crosses two chunks of one atomic unit (a slide pair, a row pair, an event series, a message thread). A chunker regression that splits the atomic unit drops the canary below the NDCG@10 ≥ 0.5 pass threshold and the canary block fails loudly.

To add a new canary: append a case to `suites/per-type-canary-suite.yaml` with `canary: true`, `canary_unit: <slide|row|event|message>`, and gold titles that span two chunks of the relevant atomic unit. The canary aggregator picks up new entries automatically; no code change needed.

### Chunk-size distribution telemetry

```bash
kairix eval chunk-stats --db-path ~/.cache/kairix/index.sqlite
```

Emits per-source-type chunk-size statistics:

```
markdown    n=243   mean=482.0  p50=512   p95=720   p99=812
pptx        n=156   mean=187.0  p50=160   p95=380   p99=520   ← small chunks → check chunker
pdf         n=890   mean=623.0  p50=512   p95=910   p99=1024
...
```

Useful for spotting **fragmentation** (long tail of tiny chunks) versus **uniformity** (near-flat distribution = chunker is over-aggressive on natural boundaries). The CLI reads `content_vectors` joined against `documents.collection` and computes chunk size from the document body length divided by chunk count per document.

### Per-type fixture corpus

`reference-library/per-type-fixtures/` ships ~120 synthetic documents across 7 source types (markdown, pptx, pdf, docx, xlsx, email, calendar). Generated by `scripts/reflib/generate_*_fixtures.py` — idempotent, seed-controlled, F32-safe (generic agent / project names only). Run the generators if a fixture shape changes:

```bash
for type in markdown pptx pdf docx xlsx email calendar; do
  python3 scripts/reflib/generate_${type}_fixtures.py
done
```

Full canary suite + measurement scaffolding evaluation runs in ~5 seconds against the bundled mock retrieval backend.

---

## Data residency

All vectors and entity data are stored locally on your own infrastructure. Vault content is sent to your configured embedding provider (Azure OpenAI, Ollama, or sentence-transformers) only for embedding. No content is retained by any third party beyond the API request.

See [SECURITY.md](../SECURITY.md) for full data handling detail.
