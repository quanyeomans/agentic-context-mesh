# Mnemosyne Benchmark Framework — Design Spec

**Version:** 1.0  
**Status:** Draft — pending Phase 1 validation  
**Purpose:** Define a reusable, agent-owner-runnable benchmark for memory/search capability assessment

---

## 1. Goal

Any agent owner should be able to:

1. Define a test suite for their own collections in a declarative YAML file
2. Run the benchmark against any retrieval system (BM25, vector, hybrid, future)
3. Get a structured report that tells them:
   - How their memory system is performing across query types
   - Whether they should expect improvements from a different retrieval architecture
   - Whether their knowledge volume is approaching a scale where current architecture will degrade

This is **not** a research benchmark. It's an operational tool for ongoing quality monitoring.

---

## 2. Test Suite Format

Each agent defines a test suite as a YAML file:

```yaml
# ~/.mnemosyne/benchmark/<agent-name>/suite.yaml
meta:
  agent: builder
  collections:            # QMD collections this agent actually uses
    - vault
    - knowledge-builder
    - builder-memory
    - tc-engineering
  version: "1.0"
  created: "2026-03-23"

cases:
  # Recall case — exact gold document
  - id: R01
    category: recall
    query: "Arize Phoenix observability tool recommendation"
    gold_path: "01-projects/202603-arize-observability-research/research-report.md"
    score_method: exact       # gold doc must appear in top-K
    notes: "Specific project research report, should rank #1"

  # LLM-judged case — no single gold doc
  - id: C01
    category: conceptual
    query: "what is the approach to memory retrieval in the platform"
    gold_path: null
    score_method: llm
    notes: "Abstract architecture question"

  # Multi-hop case
  - id: M01
    category: multi_hop
    query: "why did we choose Azure over local embeddings and what did it cost"
    gold_path: null
    score_method: llm
    notes: "Requires connecting Phase 0 decisions + cost data"
```

### Required categories

Every suite should include at least one case per category. Standard categories:

| Category | Weight | Description |
|---|---|---|
| `recall` | 0.25 | Known document retrieval — exact match against gold path |
| `temporal` | 0.20 | Time-based queries — when/what was done/decided |
| `entity` | 0.20 | Person/org/project queries — "what do we know about X" |
| `conceptual` | 0.15 | Abstract topic queries — how/why questions |
| `multi_hop` | 0.10 | Queries requiring multiple documents |
| `procedural` | 0.10 | How-to and process queries |

Weights sum to 1.0. Agents may omit categories with 0 cases (weight redistributed proportionally).

### Score methods

| Method | Description |
|---|---|
| `exact` | Gold doc path appears in top-K results. Score 1.0 or 0.0. |
| `fuzzy` | Gold doc path appears anywhere in top-K (case-insensitive substring). Score 1.0 or 0.0. |
| `llm` | LLM-as-judge rates retrieved content relevance 0.0–1.0. Uses gpt-4o-mini. |

### Recall case validity rules

A `recall` case is valid if and only if:
1. `gold_path` exists in the QMD index (verified at suite validation time)
2. `gold_path` is unique across the suite (no two cases share the same gold doc)
3. The query is specific enough to prefer the gold doc over near-neighbours (verified by running the retrieval system before committing)

---

## 3. Runner Interface

```bash
# Run against the default retrieval system
mnemosyne benchmark run --suite ~/.mnemosyne/benchmark/builder/suite.yaml

# Run against a specific system (for comparison)
mnemosyne benchmark run --suite suite.yaml --system bm25
mnemosyne benchmark run --suite suite.yaml --system hybrid
mnemosyne benchmark run --suite suite.yaml --system vector

# Validate suite (check gold paths exist, no duplicates)
mnemosyne benchmark validate --suite suite.yaml

# Compare two result files
mnemosyne benchmark compare results/B0-bm25.json results/B1-hybrid.json

# Generate suite template for an agent
mnemosyne benchmark init --agent builder --collections vault,knowledge-builder
```

---

## 4. Result Format

```json
{
  "meta": {
    "suite": "builder/suite.yaml",
    "system": "hybrid",
    "agent": "builder",
    "date": "2026-03-23",
    "run_label": "B1-hybrid-phase1"
  },
  "summary": {
    "weighted_total": 0.638,
    "n_queries": 36,
    "category_scores": {
      "recall": 0.75,
      "temporal": 0.47,
      "entity": 0.55,
      "conceptual": 0.63,
      "multi_hop": 0.44,
      "procedural": 0.55
    },
    "gate": {
      "threshold": 0.62,
      "pass": true,
      "failing_categories": []
    }
  },
  "diagnostics": {
    "vec_failure_rate": 0.0,
    "bm25_only_rate": 0.0,
    "avg_latency_ms": 1840,
    "p95_latency_ms": 3200,
    "avg_fused_count": 18.4
  },
  "cases": [ ... ]
}
```

---

## 5. Interpretation Guide

The report includes a plain-language interpretation section:

### Score thresholds

| Score | Meaning |
|---|---|
| ≥ 0.80 | Production quality. Comparable to best-in-class domain-specific systems. |
| ≥ 0.75 | Strong. Good enough for agent briefing and proactive synthesis. |
| ≥ 0.68 | Solid. Temporal and entity queries reliable. |
| ≥ 0.62 | Functional. Hybrid search working, basic entity retrieval reliable. |
| ≥ 0.51 | BM25 baseline. If you're here, you're at default QMD quality. |
| < 0.51 | Below BM25 baseline. Something is wrong with the retrieval pipeline. |

### Per-category interpretation

**Recall < 0.70:** Either your gold documents aren't indexed, or keyword overlap is low. Check: (1) are the gold paths in the index? (2) are queries specific enough?

**Temporal < 0.50:** Root cause is almost always ingestion structure, not retrieval quality. Board files and daily logs are stored as undifferentiated blobs. Fix: enable date-aware chunking in the temporal module.

**Entity < 0.55:** Entity graph either empty or not being used for boosting. Fix: populate entities.db for your key people/projects, enable entity boosting in the search pipeline.

**Conceptual < 0.55:** Hybrid RRF balance may be wrong. Try tuning RRF k (30/60/90). If BM25 alone scores higher on conceptual, the vector component is hurting.

**Multi-hop < 0.40:** Multi-hop improvement requires either a planning layer (decompose → retrieve → synthesise) or a reranker that can score document sets rather than individual docs. Neither exists in Phase 1.

**Procedural < 0.50:** If BM25 alone scored higher on procedural, the vector component is diluting keyword matches. Consider routing PROCEDURAL intent to BM25-only (already done in Phase 1 when score drops below BM25).

### Scale indicators

Include when running validate:

```
Knowledge volume summary:
  Total documents: 1784
  Vectors embedded: 6198 chunks
  Collections: vault (1655), tc-engineering (78), shape-memory (35), ...

Scale warnings:
  ⚠ vault collection is approaching 2000 docs — consider subcollection routing at 5000+
  ✓ Vector index size is healthy (6198 vectors, <1M)
  ✓ BM25 latency is nominal (P95 < 1000ms)
```

---

## 6. Suite Authoring Guide

For agent owners writing their first suite:

### Step 1: List your collections

```bash
mnemosyne benchmark init --agent <your-agent> --collections <list>
# Generates a template suite.yaml with 2 example cases per category
```

### Step 2: Add recall cases (most important)

For each case:
1. Pick a document you know should be the top result for a specific query
2. Verify it's in the index: `mnemosyne benchmark validate --check-path "<path>"`
3. Write a query that is specific to that document (not generic)
4. Test it: `mnemosyne search "<query>" --agent <agent> --json | jq '.results[0].path'`
5. If the gold doc comes back in top-3, it's a valid case. Add it.

### Step 3: Add LLM-judged cases

For temporal/entity/conceptual/multi-hop/procedural — write queries that represent real retrieval tasks your agent encounters. These don't need gold paths; the LLM judge evaluates whether the retrieved content answers the question.

Good LLM-judged cases: specific, answerable from your actual vault content.
Bad LLM-judged cases: hypothetical ("what would we do if..."), too broad ("tell me everything about X").

### Step 4: Validate and commit

```bash
mnemosyne benchmark validate --suite suite.yaml
# Outputs: N valid cases, M invalid (with reasons), estimated run time
```

Commit your suite to the repo. It becomes the regression baseline.

---

## 7. Architecture Decisions

**Why YAML, not Python?** Suite files are data, not code. Agent owners who aren't engineers should be able to read and edit them. YAML is readable; Python test files require understanding the runner internals.

**Why LLM-as-judge for non-recall cases?** Recall cases have objective answers (doc either ranks or it doesn't). Conceptual/temporal/entity queries don't — the right answer is a judgment call about relevance. LLM-as-judge is imperfect but consistent and cheap enough to run weekly.

**Why not use a public benchmark (BEIR, MTEB)?** Our retrieval problem is domain-specific. Generic benchmarks don't test temporal queries against Kanban boards or entity queries against agent memory logs. They'd tell us nothing useful about our actual failure modes.

**Why per-agent suites?** Different agents have different collections, different query patterns, and different quality requirements. Builder runs mostly procedural + recall queries. Shape runs mostly entity + temporal. A single shared suite would be dominated by the most-indexed agent's content.

---

## 8. Roadmap

| Phase | Deliverable |
|---|---|
| Phase 1 | Manual YAML suites for Builder and Shape. Runner as Python script. |
| Phase 2 | `mnemosyne benchmark` CLI with validate/run/compare subcommands. |
| Phase 3 | Automated weekly cron. Regression alerts when score drops >5%. Results committed to repo. |
| Phase 4 | `mnemosyne benchmark init` generator. Multi-agent aggregate report. |
