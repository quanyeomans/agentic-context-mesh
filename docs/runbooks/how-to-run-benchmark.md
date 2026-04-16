# How To: Run the Kairix Benchmark

**Purpose:** Run the kairix evaluation suite, interpret results, and understand when to run it.

**Suite locations:** Benchmark suites live in the `suites/` directory of this repository. Use `suites/example.yaml` as a starting point, or build your own with `kairix benchmark init`.

---

## Part A — Run the Benchmark Suite

```bash
# Standard run against production index
kairix benchmark run --suite suites/your-suite.yaml

# Run against BM25 only (to isolate vector contribution)
kairix benchmark run --suite suites/your-suite.yaml --system bm25

# Save results to file for comparison
kairix benchmark run \
  --suite suites/your-suite.yaml \
  --output ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/benchmark-results/

# Compare two saved result files
kairix benchmark compare result-a.json result-b.json
```

---

## Part B — Safe Config Tuning Workflow

To test config changes safely, use a before/after benchmark comparison:

1. Record current baseline score before making any changes
2. Edit ranking config
3. Run `kairix embed` to pick up new config
4. Run benchmark to compare
5. If score regressed: revert config via git

```bash
# 1. Record baseline
kairix benchmark run \
  --suite suites/your-suite.yaml \
  --output ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/benchmark-results/

# 2. Edit config
sudo nano /opt/kairix/config/kairix.yaml

# 3. Embed with new config (incremental — only picks up new/changed chunks)
kairix embed

# 4. Compare
kairix benchmark run \
  --suite suites/your-suite.yaml \
  --output ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/benchmark-results/
kairix benchmark compare \
  ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/benchmark-results/<before>.json \
  ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/benchmark-results/<after>.json

# 5. If worse: revert
git checkout -- /opt/kairix/config/kairix.yaml
kairix embed
```

---

## Part C — Create a New Benchmark Suite

Use this when the vault has been significantly restructured and gold paths in existing suites are stale.

```bash
# Scaffold a new suite
kairix benchmark init

# Validate an existing suite (checks gold paths exist in index)
kairix benchmark validate --suite suites/your-suite.yaml
```

The generated suite file will be placed in the current directory. Move it to `suites/` and commit it.

---

## Part D — Interpret Output

**NDCG@10** (Normalised Discounted Cumulative Gain at rank 10):
- Measures: are the most relevant results ranked highest?
- 1.0 = perfect ranking, 0.0 = worst possible

**Hit@5**: Was the expected result in the top 5? (Binary per query)

**MRR@10** (Mean Reciprocal Rank at 10): Harmonic mean of the reciprocal rank of the first correct result.

**Reading per-query output:**
- Score near 1.0 → that query is well-ranked
- Score near 0.3-0.5 → expected result is ranked 4th-10th (investigate)
- Score 0.0 → expected result not found in top 10 (embedding or content issue)

**When results are below expectations:**
- `vec=0, vec_failed=True` → [runbook-vector-search-failure](runbook-vector-search-failure.md) (credentials or dimension mismatch)
- Low scores on specific query types → [how-to-debug-search-ranking](how-to-debug-search-ranking.md)
- Gold suite paths all missing → suite is stale, vault was restructured — rebuild suite

---

## When to Run Benchmark

| Trigger | Required |
|---------|---------|
| Binary upgrade | Yes — record baseline before, compare after |
| Ranking config change | Yes — record before/after |
| Full index rebuild (`kairix embed --force`) | Yes — confirm no regression |
| Weekly health check | Recommended |
| After incident recovery | Yes |
| After vault restructure (>10 files moved/renamed) | Yes — also validate suite |

---

## Related

- [runbook-benchmark-regression](runbook-benchmark-regression.md) — if scores drop
- [how-to-upgrade-kairix](how-to-upgrade-kairix.md) — upgrade workflow using benchmark as gate
- [how-to-debug-search-ranking](how-to-debug-search-ranking.md) — if specific queries score poorly
