# Kairix — Benchmark Regression (NDCG Dropped)

**Symptom:** `kairix benchmark run` reports NDCG@10 below acceptable levels. This occurs after a config change, full re-embed cycle, embedding model change, or kairix binary upgrade.

Note: NDCG thresholds below are suggested starting points — calibrate against your own baseline before treating them as hard gates.

---

## Quick Diagnosis

```bash
# Run the benchmark suite
kairix benchmark run --suite suites/your-suite.yaml

# Check what changed recently
git log --oneline -10

# Check kairix version
kairix --version

# Check embed log for recent activity
tail -30 ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/azure-embed.log
```

---

## Root Cause A — Config Change Degraded Ranking

If NDCG dropped after an edit to kairix config (RRF weights, category scores, BM25/vector balance):

```bash
# View current ranking config
cat /opt/kairix/config/kairix.yaml | grep -A 20 "ranking:"

# Compare to last known-good (git diff)
git diff HEAD~1 -- /opt/kairix/config/kairix.yaml

# Re-run benchmark to confirm regression is reproducible
kairix benchmark run --suite suites/your-suite.yaml
```

**Fix:** Revert the ranking config to the previous version via git, then redeploy.

```bash
git checkout HEAD~1 -- /opt/kairix/config/kairix.yaml
sudo cp /opt/kairix/config/kairix.yaml /opt/kairix/config/kairix.yaml
kairix benchmark run --suite suites/your-suite.yaml
```

---

## Root Cause B — Index Re-embed Changed Chunk Quality

If NDCG dropped after a full re-embed (`kairix embed --force`):

```bash
# Check embed log for anomalies
tail -50 ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/azure-embed.log
# Look for: failed= count > 0, dimension mismatch errors, partial completion

# Check database state
sqlite3 ~/.cache/qmd/index.sqlite \
  'SELECT model, COUNT(*) FROM content_vectors GROUP BY model;'
# If mixed models present: dimension mismatch likely — run kairix embed --force again

# Run benchmark to narrow the regression
kairix benchmark run --suite suites/your-suite.yaml
```

Possible causes:
- Embed failed mid-run, leaving partial re-embed
- Vault file deleted/renamed — previously high-scoring chunk is gone
- Dimension mismatch (see [runbook-vector-search-failure](runbook-vector-search-failure.md))

---

## Root Cause C — Binary Upgrade Introduced Regression

If NDCG dropped immediately after `kairix` was upgraded to a new version:

```bash
# Check installed version
kairix --version
```

**Rollback to previous version:**

```bash
sudo /opt/kairix/.venv/bin/pip install kairix==<PREVIOUS_VERSION>

# Re-verify symlinks (pip install can reset them)
ls -la /usr/local/bin/kairix
# Must be: -> /opt/kairix/bin/kairix-wrapper.sh
# If not: sudo ln -sf /opt/kairix/bin/kairix-wrapper.sh /usr/local/bin/kairix

kairix onboard check
kairix benchmark run --suite suites/your-suite.yaml
```

---

## Root Cause D — Measurement Error

Before assuming a real regression, rule out measurement issues:

```bash
# Re-run benchmark twice — scores should be stable within ~0.01
kairix benchmark run --suite suites/your-suite.yaml
kairix benchmark run --suite suites/your-suite.yaml

# Check if gold paths in the suite still exist in the index
# (vault reorganisation can break gold path references)
tail -20 ${KAIRIX_DATA_DIR:-/var/lib/kairix}/logs/qmd-reindex.log | grep -i "gold suite"
# "WARN: N/M gold paths missing" → suite needs rebuilding, not index
```

If the gold suite itself is stale: `kairix benchmark init` to scaffold a new suite, then curate it.

---

## Verify Fix

```bash
# Benchmark must pass
kairix benchmark run --suite suites/your-suite.yaml

# Live search sanity check
kairix search "platform architecture"
kairix search "embedding cron"
# Results: BM25=N, vec=M (vec > 0), top result relevant

# System health
kairix onboard check
```

---

## Related

- [how-to-run-benchmark](how-to-run-benchmark.md) — full benchmark procedure
- [how-to-upgrade-kairix](how-to-upgrade-kairix.md) — safe upgrade with eval gate
- [runbook-emergency-recovery](runbook-emergency-recovery.md) — all-in-one quick reference
- [INDEX](INDEX.md) — full runbook registry
