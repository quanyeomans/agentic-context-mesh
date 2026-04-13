# Evaluation

Kairix is evaluated against a curated real-world benchmark derived from actual agent session queries across a personal knowledge base of ~2,800 documents. Scoring uses strict NDCG@10 with graded relevance (0 = irrelevant, 1 = partial, 2 = directly answers the query).

---

## Current Performance — v0.8.1

**NDCG@10: 0.603 · Hit@5: 0.821 · MRR@10: 0.669**

Evaluated on 95 curated cases across six query types against a real-world knowledge base (11,316 vectors at 1536-dim).

| Query type | NDCG@10 | What this means |
|---|---|---|
| Entity lookups | **0.811** | Named entities (people, organisations, concepts) surface their canonical stub plus related documents in 8 out of 10 queries |
| Keyword / proper noun | **0.599** | Version strings, error codes, specific document names resolve accurately via hybrid BM25 + vector |
| Temporal queries | **0.577** | "What happened last week", "decisions in March" route to date-scoped results |
| Procedural queries | **0.588** | How-to questions and runbook lookups return step-relevant documents ahead of tangentially related content |
| Semantic queries | 0.504 | Abstract conceptual questions retrieve relevant documents without exact term overlap |
| Multi-hop queries | 0.536 | Questions spanning multiple entities or topics decompose into sub-queries and fuse results |

A relevant document appears in the top 5 results for **82% of queries**.

---

## What good retrieval enables

The benchmark categories map directly to the use cases Kairix is built for:

**Entity-aware preparation** — "Tell me about Bupa" or "What has Avanade been working on" returns the entity's curated stub, relationship context (who works there, what projects are active), and ranked vault documents. The 0.811 entity score reflects this working reliably on real queries.

**Meeting and session prep** — temporal and multi-hop retrieval together cover queries like "what decisions were made last month about the platform architecture" or "what's the current status of the Azure connector and why was it chosen." These require date-scoped retrieval and multi-document reasoning — both categories score above 0.55.

**Procedural knowledge** — agents querying runbooks, standards, and how-to guides get step-relevant content ranked above generic background material. The 0.588 procedural score reflects path-weighted re-ranking working as intended.

**Keyword accuracy** — error codes, version strings, file paths, and proper nouns return precise results. The v0.8.1 hybrid fix (all intents run BM25 + vector in parallel) improved keyword NDCG from 0.48 → 0.60.

---

## Methodology

### Suite

Cases are defined in `suites/example.yaml`. Each specifies a query, expected gold documents, and a graded relevance score:

```yaml
- id: E-01
  category: entity
  query: "Alice Chen role and responsibilities"
  gold_paths:
    - path: entities/person/alice-chen.md
      relevance: 2
    - path: shared/team-overview.md
      relevance: 1
  score_method: ndcg
```

Gold paths use collection-relative format — collection prefixes are stripped so paths match `kairix search` output.

### Scoring

NDCG@10 with graded relevance is the primary metric. It rewards retrieving highly relevant documents at top positions and penalises ranking partially relevant documents above highly relevant ones.

| NDCG@10 | Interpretation |
|---|---|
| ≥ 0.75 | Strong — entity-quality retrieval |
| 0.60–0.75 | Solid — above typical RAG baseline for heterogeneous private knowledge |
| 0.50–0.60 | Functional — relevant content found but ranking has room to improve |
| < 0.45 | Needs attention — comparable to BM25-only baseline |

Typical RAG systems on heterogeneous personal knowledge score 0.55–0.70 on held-out curated suites. Kairix at 0.603 overall with entity at 0.811 reflects the value of entity graph awareness on top of hybrid retrieval.

### Running the benchmark

```bash
kairix benchmark run --suite suites/example.yaml
```

The public example suite (`suites/example.yaml`) contains anonymised, domain-neutral cases. The real-world scores above were measured against a private vault-specific suite that cannot be published.

---

## Data residency

All vectors and entity data are stored locally on your own infrastructure. Vault content is sent to your configured embedding provider (Azure OpenAI, Ollama, or sentence-transformers) only for embedding. No content is retained by any third party beyond the API request.

See [SECURITY.md](SECURITY.md) for full data handling detail.
