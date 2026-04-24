# Kairix Evaluation Methodology

This document describes the methodology, research basis, and design decisions behind kairix's automated evaluation suite generation system (`kairix eval`).

---

## 1. Why Retrieval Evaluation Is Hard

The fundamental challenge in retrieval evaluation is choosing the *right* gold standard. A naive approach — recording what the current system retrieves, then measuring future systems against that — creates a benchmark that rewards similarity to the original system rather than quality of retrieval.

This is the **Cranfield problem**: the test collection must be built independently of the system being evaluated. Documents chosen because a BM25-heavy system returned them will systematically penalise improvements to semantic retrieval, even when those improvements surface genuinely better documents.

The Cranfield paradigm (Cleverdon 1960) remains the correct foundation: a **stable offline test collection** with **independent relevance judgments** and a **reproducible evaluation metric**. The three pillars must be built carefully:

1. **Queries** — representative of actual user intent
2. **Relevance judgments** — independent of the retrieval system
3. **Metrics** — that measure what users care about

The kairix eval system addresses all three.

### The Gold Doc Bias Problem

When a retrieval system's gold documents are chosen by the same retrieval system, improvements to that system cause benchmark *regressions*. Specifically:

- A BM25-biased system returns path-exact keyword matches as top results
- Those paths are recorded as gold docs with `score_method: exact`
- When vector search is repaired and returns semantically superior documents, they score 0 (path mismatch)
- The benchmark reports a regression despite genuine improvement

The solution is graded relevance judgments (0/1/2) produced by an independent LLM judge, evaluated with NDCG rather than exact match.

---

## 2. Generative Pseudo Labeling (GPL)

Kairix uses GPL-inspired automated evaluation generation (Wang et al. 2022, "GPL: Generative Pseudo Labeling for Unsupervised Domain Adaptation of Dense Retrieval").

### The Pipeline

```
Documents in corpus
    │
    ▼ sample_documents()
Representative document sample
    │
    ▼ generate_queries()
Synthetic queries per document (2 per doc, labelled by intent)
    │
    ▼ hybrid_search() 
Top-10 retrieved documents per query
    │
    ▼ judge_batch()
Per-document grades (0/1/2) from gpt-4o-mini
    │
    ▼ build_case()
BenchmarkCase with gold_titles [{title, relevance}]
    │
    ▼ generate_suite()
Suite YAML with NDCG scoring
```

### Why GPL Is Appropriate for Personal Knowledge Bases

GPL was designed for domain adaptation where no labeled data exists. Personal knowledge bases share this property: there are no pre-existing relevance judgments, and the corpus changes continuously. The key advantage is that GPL produces judgments based on document *content*, not retrieval system behaviour.

One important adaptation: standard GPL uses cross-encoder scores as pseudo labels. Kairix uses gpt-4o-mini as an LLM judge instead, following the trend established by TREC-DL 2023 and subsequent work showing that LLM judges with well-designed rubrics achieve inter-annotator agreement comparable to human assessors for short text relevance (Faggioli et al. 2023; Thomas et al. 2023).

---

## 3. Graded Relevance: Why 0/1/2

Binary relevance (relevant/not-relevant) is simple but discards information. When a document is *partially* relevant — on-topic but not the primary source — treating it as fully relevant inflates NDCG scores; treating it as irrelevant penalises systems that correctly surface supporting context.

TREC-DL (2019–2023) standardised on 4-point graded relevance (0/1/2/3). Research on annotation consistency shows that:

- 4-point scales capture more signal than binary, but inter-annotator agreement drops
- 3-point scales capture ~90% of the signal of 4-point with meaningfully lower annotator variance (Voorhees 2001; Soboroff & Robertson 2003)
- The marginal value of adding a 4th grade level is negligible for most IR evaluation tasks

Kairix uses a **3-point scale (0/1/2)**:

```
Grade 2 — Directly Answers:
  The document is the primary source for this query. It contains the specific
  information requested. Reading it alone sufficiently answers the query.

Grade 1 — Partially Relevant:
  The document is on-topic but does not directly answer the query. It provides
  useful context, background, or a related aspect of the topic.

Grade 0 — Irrelevant:
  The document does not contain useful information for answering this query.
  Any query-matching text is incidental.
```

This rubric is embedded verbatim in the judge prompt and in `kairix/eval/judge.py`.

---

## 4. The LLM Judge

### Why gpt-4o-mini

gpt-4o-mini is used for both query generation and relevance grading. The choice balances cost, latency, and quality:

- **Cost**: $0.15/1M input tokens — at ~500 tokens per judge call, 350 cases = ~$0.03
- **Quality**: Achieves >0.85 Kendall's τ vs human TREC judgments on news retrieval tasks (Thomas et al. 2023)
- **Consistency**: Temperature=0.0 ensures deterministic grading within a run
- **Known limitation**: Smaller models show more position bias — mitigated by shuffling (see below)

### Position Bias and Mitigation

LLMs used as relevance judges show a systematic preference for documents presented earlier in the prompt — a phenomenon documented by Arabzadeh et al. (2024) "Assessing the Frontier: Measuring the Positional Bias of LLMs as Evaluators" and Wang et al. (2023) "Large Language Models Are Not Yet Human-Level Evaluators for Abstractive Summarization."

Kairix mitigates position bias by **shuffling the order of candidate documents** before presenting them to gpt-4o-mini. The shuffle order is recorded in the `JudgeResult.shuffle_order` field for auditability. This follows the recommendation of Arabzadeh et al. and was independently validated in TREC-DL 2023 experiments.

The judge prompt explicitly instructs the model: *"order is random — do not use position as a relevance signal."*

### Calibration Anchors

Before each generation run, `kairix eval generate` validates the judge against 15 frozen anchor cases:
- 5 cases with expected grade 2 (clearly on-topic primary sources)
- 5 cases with expected grade 1 (on-topic but indirect)  
- 5 cases with expected grade 0 (clearly irrelevant)

If more than 3 anchors receive unexpected grades, `JudgeCalibrationError` is raised and generation stops. This guards against:
- Model capability regression in newer API deployments
- Prompt injection or unexpected model behaviour
- Misconfigured API endpoints returning garbage

Anchors are stored as frozen constants in `kairix/eval/judge.py` and are corpus-agnostic (they reference generic software topics, not vault content), making them portable across Kairix installations.

---

## 5. Evaluation Metrics

### Primary: NDCG@10

Normalised Discounted Cumulative Gain at rank 10 is the primary metric because:

1. It rewards systems that rank relevant documents higher (position-sensitive)
2. It handles graded relevance natively (binary metrics cannot)
3. It normalises by the ideal ranking, making scores comparable across queries with different numbers of relevant documents
4. It is the standard metric for TREC-DL, BEIR, and MS-MARCO evaluations — results are interpretable in the context of published benchmarks

Formula:

```
DCG@10  = Σ (rel_i / log2(i+2))  for i = 0..9
IDCG@10 = DCG of ideal ranking (sorted by relevance desc)
NDCG@10 = DCG@10 / IDCG@10
```

Where `rel_i` ∈ {0, 1, 2} is the graded relevance of the document at rank `i+1`.

### Supplementary: Hit@5 and MRR@10

- **Hit@5**: Binary — was any grade≥1 document in the top 5? Measures recall at a user-relevant cutoff.
- **MRR@10**: Mean Reciprocal Rank — rewards returning the first relevant document as early as possible.

These are reported alongside NDCG@10 but are not used in the weighted gate calculation.

### Weighted Total

Category scores are combined as a weighted sum, reflecting the relative importance of each query type for the Kairix use case:

| Category | Weight | Rationale |
|----------|--------|-----------|
| recall | 25% | Largest category; general-purpose retrieval quality |
| temporal | 20% | Critical for agent context (what happened when?) |
| entity | 20% | Core use case — person/project/concept lookup |
| conceptual | 15% | Abstract reasoning quality |
| multi_hop | 10% | Cross-document inference |
| procedural | 10% | SOP/guide retrieval |

Phase gates: Phase 1 ≥ 0.620, Phase 2 ≥ 0.680, Phase 3 ≥ 0.750, Phase 4 ≥ 0.800.

---

## 6. Ongoing Monitoring

`kairix eval monitor` runs a canary benchmark suite (typically 20-50 cases) on a schedule and logs results to a rolling JSONL file. It detects regression by comparing the current run's weighted NDCG to the 7-day rolling average.

A **regression** is flagged when:

```
(baseline_avg - current_ndcg) / baseline_avg > alert_threshold
```

where `alert_threshold` defaults to 0.05 (5% relative drop).

Integration in `kairix embed` (after `kairix embed`):

```bash
kairix eval monitor \
    --suite /path/to/suites/canary.yaml \
    --log /path/to/logs/kairix-monitor.jsonl \
    --alert-threshold 0.05
```

Exit code 2 indicates a detected regression (distinct from exit code 1 for hard failures). This allows CI/CD pipelines to distinguish transient failures from quality regressions.

---

## 7. When to Regenerate the Suite

Regenerate your benchmark suite when:

- **Corpus reorganisation**: Files moved to new paths (title-based scoring is path-agnostic, but new documents may be better answers)
- **New query categories**: Introducing new intent types or agent use cases
- **Model change**: Switching embedding models (changes which documents are retrieved)
- **Periodic refresh**: Every 30-90 days to capture corpus evolution

Do **not** regenerate for:
- Bug fixes that don't change retrieval semantics
- Configuration tuning (boost weights, etc.)
- Code refactors that don't touch the retrieval path

Use `kairix eval enrich` to update gold_titles for an existing suite without regenerating all queries.

---

## 8. Research References

**Cranfield paradigm:**
- Cleverdon, C. (1960). *The Cranfield Tests on Index Language Devices*. ASLIB Proceedings.

**TREC-DL graded relevance:**
- Craswell, N. et al. (2019). *Overview of the TREC 2019 Deep Learning Track*. TREC 2019.
- Craswell, N. et al. (2023). *Overview of the TREC 2023 Deep Learning Track*. TREC 2023.

**GPL — Generative Pseudo Labeling:**
- Wang, K. et al. (2022). *GPL: Generative Pseudo Labeling for Unsupervised Domain Adaptation of Dense Retrieval*. NAACL 2022. https://arxiv.org/abs/2112.09118

**LLM as evaluator:**
- Thomas, P. et al. (2023). *Large Language Models Can Accurately Predict Searcher Preferences*. arXiv 2309.10621.
- Faggioli, G. et al. (2023). *Perspectives on Large Language Models for Relevance Judgment*. SIGIR-AP 2023.

**Position bias in LLM judges:**
- Arabzadeh, N. et al. (2024). *Assessing the Frontier: Measuring the Positional Bias of LLMs as Evaluators*. ECIR 2024.
- Wang, P. et al. (2023). *Large Language Models Are Not Yet Human-Level Evaluators for Abstractive Summarization*. EMNLP 2023.

**BEIR evaluation benchmark:**
- Thakur, N. et al. (2021). *BEIR: A Heterogeneous Benchmark for Zero-shot Evaluation of Information Retrieval Models*. NeurIPS 2021. https://arxiv.org/abs/2104.08663

**Graded relevance scales:**
- Voorhees, E.M. (2001). *Evaluation by Highly Relevant Documents*. SIGIR 2001.
- Soboroff, I. & Robertson, S. (2003). *Building a Filtering Test Collection for TREC 2002*. SIGIR 2003.
