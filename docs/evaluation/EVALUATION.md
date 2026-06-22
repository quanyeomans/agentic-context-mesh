# Evaluation

Kairix is evaluated against a curated real-world benchmark derived from actual agent session queries across your knowledge base. Scoring uses strict NDCG@10 with graded relevance (0 = irrelevant, 1 = partial, 2 = directly answers the query).

---

## Current Performance — v2026.6.9 measurement (standing baseline)

**Weighted total: 0.808 · NDCG@10: 0.884 · Hit@5: 0.913 · MRR@10: 0.831**

This is the standing benchmark — the v2026.6.9 measurement against the 242-case `reflib` production suite. No newer full sweep has been published; the latest release is v2026.6.18.

Evaluated on a 242-case reference library gold suite across six query categories. (The #453 Phase B expansion grew the harder categories — entity to 15, temporal to 20, multi-hop to 15 — which is why temporal now scores well below its old number on the tougher suite.)

| Query category | NDCG@10 | n | Weight | What this means |
|---|---|---|---|---|
| Recall | **0.916** | 54 | 25% | "What happened with X" / find-the-document queries surface the right notes via hybrid BM25 + vector |
| Temporal | **0.558** | 20 | 20% | "What happened last week", "decisions in March" route to date-scoped results — **weakest category** on the expanded suite |
| Entity | **0.800** | 15 | 20% | Named entities (people, organisations, concepts) surface canonical stub plus related documents; see note below |
| Conceptual | **0.917** | 75 | 15% | Abstract conceptual questions retrieve relevant documents without exact term overlap |
| Multi-hop | **0.724** | 15 | 10% | Questions spanning multiple entities or topics decompose into sub-queries and fuse results |
| Procedural | **0.977** | 63 | 10% | How-to questions and runbook lookups return step-relevant documents ahead of tangentially related content |

A relevant document appears in the top 5 results for **91% of queries** (Hit@5 0.913 on this suite).

> **Temporal note:** Temporal is the weakest category at 0.558 — the #453 Phase B expansion quadrupled the temporal case count (5 → 20) with harder date-scoped queries, and improving temporal retrieval is the active focus. Don't treat it as solved.

> **Entity NDCG note:** Entity-summary indexing (ADR-036, flag `entity_summary_indexing_enabled`) shipped in v2026.6.9 and lifts entity retrieval by indexing each entity's curated summary alongside its documents. Entity scores 0.800 on the 15 expanded entity cases; further gains are expected as the Neo4j entity graph densifies.

## What good retrieval enables

The benchmark categories map directly to the use cases Kairix is built for:

**Entity-aware preparation** — "Tell me about Acme Corp" or "What has TechCorp been working on" returns the entity's curated stub, relationship context (who works there, what projects are active), and ranked documents. Entity scores 0.800 with entity-summary indexing (ADR-036) live; further gains are expected as the Neo4j graph densifies.

**Meeting and session prep** — temporal and multi-hop retrieval together cover queries like "what decisions were made last month about the platform architecture" or "what's the current status of the Azure connector and why was it chosen." These require date-scoped retrieval and multi-document reasoning. Multi-hop holds at 0.724, but temporal is the weakest category at 0.558 on the expanded suite — date-scoped retrieval is the active improvement focus.

**Procedural knowledge** — agents querying runbooks, standards, and how-to guides get step-relevant content ranked above generic background material. The 0.977 procedural score reflects path-weighted re-ranking working as intended — the strongest category on the suite.

**Recall and conceptual accuracy** — find-the-document recall queries (0.916) and abstract conceptual questions (0.917) both retrieve relevant documents without exact term overlap. The hybrid search design (BM25 + vector in parallel for all intents) drives these top scores.

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

Cases are defined in the packaged suite `kairix/data/suites/example.yaml` (suites ship inside the wheel; there is no top-level `suites/` directory). Each specifies a query, the expected relevant documents by title, and a graded relevance score:

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
kairix benchmark run --suite kairix/data/suites/example.yaml
```

`kairix benchmark` runs a suite and reports scores (it also folds in the former `kairix probe` / `kairix soak` latency and soak modes). The richer eval surface — gold-suite build, LLM-judge, sweep, monitor, and phase gate — lives under `kairix eval`; that CLI resolves the packaged suite and the deployment's index automatically (`kairix/quality/eval/cli.py`, #552), so you usually don't pass a `--suite` path or DB path by hand.

The CLI output reports both the **weighted total** (category-weighted average used for phase gates) and **NDCG@10** (the standard IR metric, computed per-case and averaged across all `ndcg`-scored cases):

```
============================================================
BENCHMARK RESULTS
============================================================
Weighted total: 0.808   [Strong]
NDCG@10:       0.884   (Hit@5: 0.913  MRR@10: 0.831)

Category breakdown:
  recall       0.916  (weight 25%, n=54)  ...
  temporal     0.558  (weight 20%, n=20)  ...
  entity       0.800  (weight 20%, n=15)  ...
  conceptual   0.917  (weight 15%, n=75)  ...
  multi_hop    0.724  (weight 10%, n=15)  ...
  procedural   0.977  (weight 10%, n=63)  ...

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

The public example suite (`kairix/data/suites/example.yaml`) contains anonymised, domain-neutral cases. The real-world scores above were measured against the 242-case private `reflib` suite that cannot be published.

> **Clean reference-library upper bound.** A separate clean reference-library sweep (`kairix/data/suites/reflib-gold-v3.yaml`, run 2026-05-08) reports hybrid-RRF **NDCG@10 0.949 / Hit@5 0.965**. That is an upper bound on a purpose-built clean corpus — keep it distinct from the 242-case production baseline above; the two are not the same measurement and should not be conflated.

---

## Per-source-type evaluation (ADR-028 scaffolding)

The eval harness reports **three measurement surfaces** that ADR-028 needs to evaluate per-type chunker plugins. None of them change the existing overall NDCG@10 number — they layer additional detail on top.

### Per-source-type Recall@k slicing

`BenchmarkResult.summary["per_source_type"]` carries one row per source type with NDCG@10, MRR@10, Hit@10, and query count. Source type is derived from each gold answer's title or path extension, with an explicit `source_type:` field on the suite case as the override.

A query asking about a `.pptx` deck contributes to the `pptx` slice; a query asking about a `.md` note contributes to `markdown`. Catastrophic failure on one type (a chunker that breaks XLSX rows) shows up as a slice that lags well below the overall NDCG@10 — invisible in the global average.

### Boundary-spanning canary suite

`kairix/data/suites/per-type-canary-suite.yaml` ships 12 canary queries — three per atomic unit type (slide, row, event, message). Each canary's gold answer deliberately crosses two chunks of one atomic unit (a slide pair, a row pair, an event series, a message thread). A chunker regression that splits the atomic unit drops the canary below the NDCG@10 ≥ 0.5 pass threshold and the canary block fails loudly.

To add a new canary: append a case to `kairix/data/suites/per-type-canary-suite.yaml` with `canary: true`, `canary_unit: <slide|row|event|message>`, and gold titles that span two chunks of the relevant atomic unit. The canary aggregator picks up new entries automatically; no code change needed.

### Chunk-size distribution telemetry

```bash
kairix eval chunk-stats   # resolves the deployment's index automatically
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
