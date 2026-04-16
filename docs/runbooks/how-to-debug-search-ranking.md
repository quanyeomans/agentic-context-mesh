# How To: Debug Hybrid Search Ranking

**Purpose:** Diagnose why search results are ranked incorrectly — wrong order, missing high-relevance results, or unexpected content appearing at the top.

**Background:** Kairix uses Reciprocal Rank Fusion (RRF) to combine BM25 (keyword) and vector (semantic) scores, then applies category-based boosts and entity graph enrichment. Ranking issues usually stem from one of: weight imbalance, missing embeddings, or category misconfiguration.

---

## Step 1 — Enable Debug Mode

```bash
# Run the query with debug output
kairix search "your query here" --debug

# Debug output shows:
# Query intent: <procedural|conceptual|entity|temporal>
# Dispatch: BM25 + vector (or BM25 only if vec_failed)
# Per-result breakdown:
#   [1] score=0.92 | bm25=0.85 | vec=0.78 | rrf=0.92 | cat_boost=+0.10 | entity_boost=+0.00
#       source: 01-Projects/BRIEF.md
#   [2] score=0.88 | bm25=0.72 | vec=0.91 | rrf=0.88 | cat_boost=+0.00 | entity_boost=+0.05
```

---

## Step 2 — Identify the Failure Pattern

### Pattern A: Expected result is missing entirely

```bash
# Check if the document is in the index at all
kairix search "exact phrase from the document" --debug
# If not found: chunk may not be embedded yet → runbook-embedding-lag

# Check when the file was last embedded
tail -50 ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/azure-embed.log | grep "filename.md"
```

### Pattern B: Expected result is in results but ranked too low

```bash
# Check its score breakdown (--debug)
# Low bm25 + low vec → content is genuinely not matching the query
# High vec + low rrf → RRF weighting may be suppressing semantic results
# Missing cat_boost → category not matched or category scores misconfigured

# View current category scoring config
cat /opt/kairix/config/kairix.yaml | grep -A 30 "categories:"
```

### Pattern C: Irrelevant result ranked #1

```bash
# Check if it has an unusually high cat_boost
kairix search "query" --debug | head -20
# If cat_boost is inflating an irrelevant result: misconfigured category weights

# Check entity_boost inflation
# Entity boost is applied when an entity in the result matches a named entity in the query
# If a common entity appears in too many unrelated chunks: over-boosting
```

### Pattern D: vec_failed=True (no vector results)

```bash
# Vector search silently failed
kairix search "query" --debug
# Shows: "vec_failed: True, reason: credential_error"
# Fix: runbook-vector-search-failure
```

---

## Step 3 — Inspect the Ranking Config

```bash
# View the full ranking configuration
cat /opt/kairix/config/kairix.yaml | grep -A 50 "ranking:"

# Key parameters:
# rrf_k: 60                    # RRF constant (higher = flatter ranking)
# bm25_weight: 0.4             # Weight of BM25 in final RRF score
# vec_weight: 0.6              # Weight of vector in final RRF score
# entity_boost: 0.15           # Boost for entity-graph-confirmed results
#
# categories:
#   ops: 1.2                   # Runbooks/operations content
#   architecture: 1.1          # Architecture documents
#   projects: 1.0              # Project briefs (neutral)
#   knowledge: 0.9             # Reference material
```

---

## Step 4 — Tune Weights (Safe Workflow)

Use the benchmark before/after approach to validate any changes before committing them:

```bash
# Record baseline before any changes
kairix benchmark run \
  --suite suites/your-suite.yaml \
  --output ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/benchmark-results/

# Backup and edit config
cp /opt/kairix/config/kairix.yaml /opt/kairix/config/kairix.yaml.bak
sudo nano /opt/kairix/config/kairix.yaml
# Adjust rrf_k, bm25_weight, vec_weight, category boosts

# Run incremental embed to pick up new config
kairix embed

# Run benchmark and compare
kairix benchmark run \
  --suite suites/your-suite.yaml \
  --output ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/benchmark-results/
kairix benchmark compare \
  ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/benchmark-results/<before>.json \
  ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/benchmark-results/<after>.json

# If scores regressed → revert
sudo cp /opt/kairix/config/kairix.yaml.bak /opt/kairix/config/kairix.yaml
kairix embed
```

---

## Step 5 — Query Intent Dispatch

Kairix classifies each query by intent before dispatching. Misclassification can cause poor results.

```bash
# See query intent in debug output
kairix search "how to restart services" --debug
# Expected intent: procedural
# If classified as "conceptual" → BM25 given too much weight for this query type

# Intent types and their dispatch:
# procedural  → BM25 weight higher (good for how-to, step-by-step)
# conceptual  → vector weight higher (good for "what is X", "why")
# entity      → entity graph queried first, then hybrid
# temporal    → timeline results prioritised
```

If intent classification is consistently wrong for a query type, flag for product backlog.

---

## Step 6 — Verify After Tuning

```bash
# Run the original failing query
kairix search "your query" --debug
# Confirm expected result is now #1 or top-3

# Run benchmark to ensure no regression
kairix benchmark run --suite suites/your-suite.yaml
```

---

## Related

- [runbook-benchmark-regression](runbook-benchmark-regression.md) — if NDCG drops after tuning
- [runbook-embedding-lag](runbook-embedding-lag.md) — if document is missing from index entirely
- [how-to-run-benchmark](how-to-run-benchmark.md) — benchmark workflow
