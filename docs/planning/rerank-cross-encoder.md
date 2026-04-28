# Planning: Cross-Encoder Re-Ranking

> **Status: Superseded (2026-04-27).** Cross-encoder re-ranking is shipped as an optional feature (`kairix[rerank]`). Current baseline exceeds all targets in this plan.

**Target version:** v1.0.0
**Primary motivation:** Semantic NDCG@10 is 0.501 (current baseline) — the weakest retrieval category. Cross-encoder re-ranking is the established intervention for lifting semantic precision without changing the retrieval pipeline.

---

## Problem statement

Hybrid BM25 + vector search with RRF fusion returns good candidates but ranks them by a surrogate signal (RRF score). For semantic queries — abstract conceptual questions with no exact term overlap — the top RRF result is often only the second or third most relevant document. A cross-encoder reads (query, document) pairs jointly and produces a direct relevance score, which makes it a much stronger ranker than RRF for these cases.

Current category scores (v0.9.0):
- Semantic: **0.501** — the floor we're targeting
- Multi-hop: 0.526 — secondary beneficiary
- All other categories at ≥ 0.540 — these should not regress

---

## Approach

Use `cross-encoder/ms-marco-MiniLM-L-6-v2` from Hugging Face `sentence-transformers`. This model:
- Is tuned on MS MARCO (passage ranking), which transfers well to personal knowledge base retrieval
- Runs on CPU in < 50ms for a batch of 10 (query, passage) pairs
- Is 22 MB — ships in the Docker image without meaningful size impact
- Has no license restriction for private deployment

Pipeline change: after RRF fusion returns top-K results, pass the top-N (default 20) to the cross-encoder; re-sort by cross-encoder score; return top-K from re-sorted list.

```
Query
  │
  ├── BM25 (top-40) ──┐
  │                    ├── RRF fusion → top-20 candidates
  └── Vector (top-40)─┘
                            │
                            ▼
                   Cross-encoder score (query, doc_text) × 20 pairs
                            │
                            ▼
                   Re-sorted top-10 results
```

---

## Implementation plan

### New file: `kairix/search/rerank.py`

```python
"""
kairix.search.rerank
~~~~~~~~~~~~~~~~~~~~

Cross-encoder re-ranking pass for hybrid search results.

Inputs:
  query: str — the original user query
  results: list[FusedResult] — top-N RRF-fused candidates (N ≤ RERANK_CANDIDATE_LIMIT)

Output:
  list[FusedResult] — same objects, sorted descending by cross-encoder score.
  `rerank_score` field added to each result.

Failure modes:
  - Model unavailable or import error: returns results unchanged, logs WARNING.
  - Empty results list: returns [] immediately.
  - Single result: returns unchanged (no ranking benefit).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kairix.search.fuse import FusedResult

logger = logging.getLogger(__name__)

RERANK_CANDIDATE_LIMIT = 20   # Max candidates passed to cross-encoder. A/B tested vs 10 and 30.
RERANK_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"

_cross_encoder: "CrossEncoder | None" = None  # lazy singleton


def _get_cross_encoder() -> "CrossEncoder":
    global _cross_encoder
    if _cross_encoder is None:
        from sentence_transformers import CrossEncoder  # type: ignore[import]
        _cross_encoder = CrossEncoder(RERANK_MODEL)
    return _cross_encoder


def rerank(query: str, results: list[FusedResult]) -> list[FusedResult]:
    """Re-sort results by cross-encoder relevance score.

    Returns results unchanged if fewer than 2 candidates, or if model fails to load.
    """
    if len(results) < 2:
        return results

    candidates = results[:RERANK_CANDIDATE_LIMIT]

    try:
        ce = _get_cross_encoder()
        pairs = [(query, r.text) for r in candidates]
        scores: list[float] = ce.predict(pairs).tolist()
    except Exception as exc:
        logger.warning("rerank: cross-encoder unavailable, returning RRF order — %s", exc)
        return results

    for result, score in zip(candidates, scores):
        result.rerank_score = score

    reranked = sorted(candidates, key=lambda r: r.rerank_score, reverse=True)
    # Append any results beyond RERANK_CANDIDATE_LIMIT in original RRF order
    return reranked + results[RERANK_CANDIDATE_LIMIT:]
```

### Changes to `kairix/search/hybrid.py`

Add `--rerank` / `KAIRIX_RERANK` flag gating:

```python
RERANK_DEFAULT = False          # Off by default until latency benchmarked on VM
RERANK_CANDIDATE_LIMIT = 20     # Imported from rerank.py; duplicated here as doc reference

def hybrid_search(
    query: str,
    ...,
    rerank: bool = RERANK_DEFAULT,
) -> list[FusedResult]:
    ...
    fused = rrf_fuse(bm25_results, vec_results)
    if rerank:
        from kairix.search.rerank import rerank as _rerank
        fused = _rerank(query, fused)
    return fused[:k]
```

`KAIRIX_RERANK=1` env var enables it at deploy time without a code change.

### Changes to `kairix/search/fuse.py` (FusedResult)

Add optional field:

```python
@dataclass
class FusedResult:
    ...
    rerank_score: float = 0.0   # Set by rerank.py; 0.0 if re-ranking not applied
```

### Changes to `kairix/cli.py`

Add `--rerank` flag to `search` subcommand:

```bash
kairix search "query" --rerank
```

### Changes to `kairix/mcp/server.py`

Pass `rerank=True` when `KAIRIX_RERANK` is set:

```python
rerank_enabled = os.environ.get("KAIRIX_RERANK", "0") == "1"
results = hybrid_search(query, ..., rerank=rerank_enabled)
```

---

## New dependency

```toml
# pyproject.toml [dependencies]
"sentence-transformers>=2.7,<3.0"
```

Model download: first call to `_get_cross_encoder()` downloads ~22 MB from Hugging Face Hub to `~/.cache/huggingface/hub/`. In Docker, pre-download at image build time:

```dockerfile
# Dockerfile — add after pip install
RUN python -c "from sentence_transformers import CrossEncoder; CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')"
```

This bakes the model into the image layer (~22 MB) so there is no runtime download.

---

## Tests

### `tests/search/test_rerank.py`

| Test | Type | Description |
|---|---|---|
| `test_rerank_sorts_by_score` | unit | Mock CrossEncoder.predict returns known scores; assert output order |
| `test_rerank_returns_unchanged_on_single_result` | unit | No CE call; original list returned |
| `test_rerank_returns_unchanged_on_empty` | unit | No CE call; [] returned |
| `test_rerank_falls_back_on_model_error` | unit | CE.predict raises; original order preserved |
| `test_rerank_candidate_limit` | unit | 25 candidates passed; only top-20 re-ranked; remaining 5 appended in order |
| `test_rerank_score_field_populated` | unit | After rerank(), FusedResult.rerank_score is non-zero for top-N |
| `test_rerank_integration` | integration | Real CrossEncoder loaded; 5 real (query, text) pairs; top result verified |

### Changes to `tests/search/test_hybrid.py`

- Add `test_hybrid_search_with_rerank_flag` — mock `rerank.rerank`, assert called when `rerank=True`
- Add `test_hybrid_search_rerank_disabled_by_default` — assert `rerank.rerank` not called without flag

Coverage target: `kairix/search/rerank.py` ≥ 85% (per ENGINEERING.md `search/` target).

---

## Rollout sequence

1. Implement `rerank.py` + `FusedResult.rerank_score` field — all tests pass
2. Wire `--rerank` flag to CLI and MCP server (off by default)
3. Re-run benchmark with `--rerank` on the 95-case VM suite:
   - Measure per-category delta; expect semantic ≥ +0.05, no category to regress
   - Capture latency: p50 and p95 for `kairix search` with and without `--rerank`
4. If semantic NDCG improves and latency p95 < 500ms: set `RERANK_DEFAULT = True`, update EVALUATION.md
5. If latency exceeds threshold on VM hardware: investigate model quantisation (`ms-marco-MiniLM-L-6-v2-ONNX`)

---

## Success criteria

| Metric | Target |
|---|---|
| Semantic NDCG@10 | ≥ 0.560 (was 0.501) |
| Overall NDCG@10 | ≥ 0.610 (was 0.587) |
| No category regression | All categories ≥ v0.9.0 baseline |
| Latency (rerank=True, p95) | < 500ms on VM hardware |
| Latency (rerank=False) | Unchanged from v0.9.0 baseline |

---

## Dependencies and constraints

- `sentence-transformers>=2.7` must pass `pip-audit` (no known CVEs at time of writing)
- Model download at image build time — no internet access required at runtime
- Opt-in flag default (`RERANK_DEFAULT = False`) until VM latency measured; this protects existing deployments
- `rerank.py` must degrade gracefully when `sentence-transformers` is not installed (import guard, WARNING log, original order returned)
