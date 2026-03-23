# Benchmark Results

All benchmark runs against the Mnemosyne retrieval system.

## File naming convention

`B<phase>-<system>-<label>-YYYY-MM-DD.json`

- `B0` — Phase 0 baseline (50-query suite, different from Phase 1+)
- `B1` — Phase 1 suite (39 queries, 6 categories, 0.0–1.0 scoring)

## Canonical Phase 1 comparison (same 39-query suite)

| File | System | Weighted Total |
|---|---|---|
| B1-bm25-phase1-baseline-2026-03-22.json | BM25 only | 0.389 |
| B1-hybrid-phase1-fixed-2026-03-22.json | Hybrid (BM25+vector+RRF) | 0.610 |

## Phase 0 baselines (50-query suite — different from Phase 1)

| File | System | Weighted Total | Notes |
|---|---|---|---|
| B0-qmd-bm25-rerun-2026-03-22.json | BM25 | 0.505 | Real baseline (original 0.233 was broken endpoint) |
| B0-qmd-vector-2026-03-22.json | Azure vector only | 0.384 | Shows why pure vector is not a replacement |
