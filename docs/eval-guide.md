# Kairix Eval — User Guide

`kairix eval` generates benchmark suites, enriches existing suites, monitors retrieval quality, and reports on trends — all automated with gpt-4o-mini as relevance judge.

For the methodology and research basis, see [evaluation-methodology.md](evaluation-methodology.md).

> **Current baseline (v2026.4.27):** weighted 0.8171, NDCG@10 0.8385.

---

## Prerequisites

- `kairix` installed and configured
- `KAIRIX_KV_NAME` env var set (for Azure Key Vault credential resolution), **or**
  `KAIRIX_LLM_API_KEY` + `KAIRIX_LLM_ENDPOINT` set directly
- Kairix index populated (`kairix embed` completed)
- `kairix benchmark` working (`kairix benchmark validate --suite <suite>`)

---

## Quickstart: Generate Your First Suite

```bash
# Generate 100 cases from your document corpus
kairix eval generate \
  --output suites/generated.yaml \
  --count 100

# Validate the generated suite
kairix benchmark validate --suite suites/generated.yaml

# Run a benchmark against the generated suite
kairix benchmark run \
  --suite suites/generated.yaml \
  --output benchmark-results/
```

That's it. The generate command:
1. Runs calibration anchors (15 frozen test cases) to verify the judge is working
2. Samples documents from your document corpus
3. Prompts gpt-4o-mini to generate retrieval queries for each document
4. Runs hybrid search for each query
5. Judges the top-10 retrieved documents with graded relevance (0/1/2)
6. Writes accepted cases to the output YAML

---

## Command Reference

### `kairix eval generate`

Generate a new benchmark suite from your corpus.

```
kairix eval generate \
  --output PATH           # Required. Output suite YAML path.
  [--count N]             # Target case count (default: 100)
  [--categories LIST]     # Comma-separated: recall,temporal,entity,conceptual,multi_hop,procedural
  [--db PATH]             # Kairix database path (default: ~/.cache/kairix/index.sqlite)
  [--deployment NAME]     # Azure deployment (default: gpt-4o-mini)
  [--no-calibrate]        # Skip calibration anchor check (faster, less safe)
  [--seed N]              # Random seed for reproducibility
  [--agent NAME]          # Agent for retrieval scoping (default: shape)
```

**Examples:**

```bash
# Generate 50 temporal and entity cases only
kairix eval generate \
  --output suites/temporal-entity.yaml \
  --count 50 \
  --categories temporal,entity

# Reproducible generation (same docs and queries each time)
kairix eval generate \
  --output suites/reproducible.yaml \
  --seed 42

# Skip calibration (e.g. during development)
kairix eval generate --output /tmp/test.yaml --count 10 --no-calibrate
```

**Output format:** Standard suite YAML with `gold_titles` (graded relevance) and `score_method: ndcg`.

---

### `kairix eval enrich`

Convert an existing suite's `gold_path`-based cases to graded `gold_titles`.

Use this when you have an existing suite (e.g. manually curated or created when BM25 dominated) and want to upgrade it to graded relevance without regenerating all queries.

```
kairix eval enrich \
  --suite PATH            # Required. Input suite YAML.
  --output PATH           # Required. Output suite YAML (can equal --suite for in-place).
  [--db PATH]             # Kairix database path
  [--deployment NAME]     # Azure deployment (default: gpt-4o-mini)
  [--agent NAME]          # Agent for retrieval scoping (default: shape)
```

**Example:**

```bash
# Enrich the existing v2 suite in-place
kairix eval enrich \
  --suite suites/v2-real-world.yaml \
  --output suites/v2-real-world-enriched.yaml

# Validate and run
kairix benchmark validate --suite suites/v2-real-world-enriched.yaml
kairix benchmark run --suite suites/v2-real-world-enriched.yaml --output benchmark-results/
```

**What changes:**
- `gold_titles: [{title, relevance}]` added (graded 0/1/2)
- `score_method` updated to `ndcg`
- All other fields preserved unchanged

**What doesn't change:** Case IDs, categories, queries, notes, agent overrides.

---

### `kairix eval monitor`

Run a canary suite and check for retrieval regression.

```
kairix eval monitor \
  --suite PATH            # Required. Canary suite YAML.
  [--log PATH]            # Monitor log (default: KAIRIX_MONITOR_LOG or ~/.cache/kairix/monitor.jsonl)
  [--alert-threshold F]   # Relative NDCG drop to flag regression (default: 0.05)
  [--window-days N]       # Rolling baseline window in days (default: 7)
  [--agent NAME]          # Agent for retrieval scoping (default: shape)
```

**Exit codes:**
- `0` — success, no regression
- `1` — hard failure (suite not found, benchmark error)
- `2` — regression detected

**Example — integration in reindex script:**

```bash
# In kairix embed, after kairix embed:
kairix eval monitor \
  --suite /path/to/suites/canary.yaml \
  --log /path/to/logs/kairix-monitor.jsonl \
  --alert-threshold 0.05
EXIT=$?
if [ $EXIT -eq 2 ]; then
  echo "ALERT: Retrieval quality regression detected!" >&2
  # notify via your preferred channel
fi
```

**Creating a canary suite:**
A canary suite should be 20-50 cases covering all categories. Generate one from your full suite:

```bash
# Generate a small canary suite
kairix eval generate \
  --output suites/canary.yaml \
  --count 30 \
  --seed 42
```

---

### `kairix eval report`

Generate a markdown report from the monitor log.

```
kairix eval report \
  [--log PATH]            # Monitor log (default: KAIRIX_MONITOR_LOG or ~/.cache/kairix/monitor.jsonl)
  [--days N]              # Days of history to include (default: 30)
  [--output PATH]         # Markdown output path (stdout if omitted)
```

**Examples:**

```bash
# Print report to stdout
kairix eval report --days 14

# Write report to file
kairix eval report --days 30 --output docs/quality-report.md
```

---

## Interpreting Results

### NDCG@10 score ranges

| Score | Interpretation |
|-------|---------------|
| ≥ 0.80 | Excellent — Phase 4 target |
| ≥ 0.75 | Production quality — Phase 3 gate |
| ≥ 0.68 | Good — Phase 2 gate |
| ≥ 0.62 | Acceptable — Phase 1 gate |
| < 0.62 | Below baseline — investigate |

### Reading gold_titles

```yaml
gold_titles:
  - title: docker-deployment-guide    # filename stem (path-agnostic)
    relevance: 2                       # primary source — directly answers query
  - title: ci-cd-pipeline-overview
    relevance: 1                       # on-topic, provides context
```

A case is scored by whether retrieved documents match these titles (by stem), weighted by relevance grade.

### When generation rejects cases

A case is rejected if no retrieved document scores grade 2. Common reasons:

- **Query too vague**: The generated query doesn't have a clear primary answer in the corpus
- **Document too short**: Short documents produce queries that many documents can answer equally
- **Dense topic area**: Many documents are equally relevant — none scores grade 2

To increase acceptance rate: use `--count` larger than your target (the pipeline will find enough accepted cases), or narrow `--categories` to document types with clear primary answers.

---

## Setting Up Proactive Monitoring

1. **Generate a canary suite** (30-50 representative cases):
   ```bash
   kairix eval generate --output suites/canary.yaml --count 40 --seed 42
   ```

2. **Run it once to establish baseline**:
   ```bash
   kairix eval monitor --suite suites/canary.yaml
   ```

3. **Add to your reindex script** (`kairix embed`):
   ```bash
   kairix eval monitor \
     --suite /path/to/canary.yaml \
     --log /path/to/logs/kairix-monitor.jsonl
   ```

4. **Review weekly**:
   ```bash
   kairix eval report --days 7
   ```

---

## Troubleshooting

### "Calibration failed"

The LLM judge is returning unexpected grades on anchor cases. Possible causes:

- API endpoint misconfigured (returns errors or empty responses)
- Model deployment renamed (update `KAIRIX_LLM_MODEL` or use `--deployment`)
- Temporary API degradation — retry in a few minutes

Use `--no-calibrate` to bypass for development/testing. Do not bypass calibration in production generation runs.

### "No documents returned from sample_documents"

- Check `--db` path points to a populated kairix index
- Run `kairix embed` if the index is empty
- Verify the index has documents: `sqlite3 ~/.cache/kairix/index.sqlite "SELECT COUNT(*) FROM documents;"`

### NDCG scores look too low

After generating a new suite:
1. Validate: `kairix benchmark validate --suite your-suite.yaml`
2. Check acceptance rate — if <30%, the corpus may not have clear primary-source documents for many queries
3. Try `--count` 3× your target to ensure enough accepted cases

### Enrichment skips many cases

`enrich_suite` skips cases where no retrieved document scores grade≥1. This usually means:
- The original gold doc was a path-exact match that vector search doesn't rank highly
- The query is too broad and retrieves many partially-relevant but no clearly-relevant docs

For skipped cases, the original `gold_path` is preserved. You can manually add `gold_titles` for these cases if needed.
