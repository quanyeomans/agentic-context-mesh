"""Search quality metrics: NDCG, MRR, Hit@K.

Supports both binary relevance (list[str] gold paths) and graded relevance
(list[dict] with {"path": ..., "relevance": 0/1/2}).

The binary-relevance functions (ndcg_score, hit_at_k, mean_reciprocal_rank) are
the original API. The graded-relevance functions (dcg, ideal_dcg, ndcg_graded,
hit_at_k_graded, reciprocal_rank_graded) were extracted from
kairix.quality.benchmark.runner so both modules share the same DCG math.
"""

from __future__ import annotations

import math

# ---------------------------------------------------------------------------
# Core DCG helpers (shared by binary and graded variants)
# ---------------------------------------------------------------------------


def dcg(relevances: list[float], k: int | None = None) -> float:
    """Discounted Cumulative Gain for a ranked list of relevance scores.

    Args:
        relevances: Per-rank relevance values.
        k: Optional rank cutoff. None means use all.
    """
    items = relevances[:k] if k is not None else relevances
    return sum(rel / math.log2(rank + 2) for rank, rel in enumerate(items))


def ideal_dcg(relevances: list[float], k: int) -> float:
    """Ideal DCG: best possible ordering of relevances up to rank k."""
    ideal = sorted(relevances, reverse=True)[:k]
    return dcg(ideal)


# Keep underscore aliases for internal callers
_dcg = dcg
_ideal_dcg = ideal_dcg


# ---------------------------------------------------------------------------
# Binary-relevance API (gold_paths: list[str])
# ---------------------------------------------------------------------------


def ndcg_score(retrieved_paths: list[str], gold_paths: list[str], k: int = 10) -> float:
    """
    Normalised Discounted Cumulative Gain at rank k.

    Args:
        retrieved_paths: Ordered list of retrieved document paths.
        gold_paths: Set of relevant document paths (all equally relevant, score=1).
        k: Rank cutoff.

    Returns:
        NDCG@k in [0.0, 1.0]. Returns 0.0 when gold_paths is empty.
    """
    if not gold_paths:
        return 0.0
    gold_set = set(gold_paths)
    relevances = [1.0 if p in gold_set else 0.0 for p in retrieved_paths[:k]]
    ideal_rel = [1.0] * min(len(gold_paths), k)
    idcg = _ideal_dcg(ideal_rel, k)
    if idcg == 0.0:
        return 0.0
    return _dcg(relevances) / idcg


def hit_at_k(retrieved_paths: list[str], gold_paths: list[str], k: int = 10) -> float:
    """
    Hit@K: 1.0 if any gold path appears in top-k retrieved, else 0.0.
    """
    if not gold_paths:
        return 0.0
    gold_set = set(gold_paths)
    return 1.0 if any(p in gold_set for p in retrieved_paths[:k]) else 0.0


def mean_reciprocal_rank(retrieved_paths: list[str], gold_paths: list[str]) -> float:
    """
    Mean Reciprocal Rank: 1/rank of first relevant result (0.0 if none found).
    """
    if not gold_paths:
        return 0.0
    gold_set = set(gold_paths)
    for rank, path in enumerate(retrieved_paths, start=1):
        if path in gold_set:
            return 1.0 / rank
    return 0.0


# ---------------------------------------------------------------------------
# Graded-relevance API (gold_paths: list[dict] with "path" and "relevance")
#
# Uses suffix-based path matching: a retrieved path matches a gold entry if
# either is a suffix of the other (case-insensitive, backslash-normalised).
# ---------------------------------------------------------------------------


def _path_relevance(path: str, gold_map: dict[str, float]) -> float:
    """Return relevance of a retrieved path via exact then suffix match against gold_map."""
    p = path.lower().replace("\\", "/")
    if p in gold_map:
        return gold_map[p]
    parts = p.split("/")
    for n in range(1, len(parts)):
        suffix = "/".join(parts[n:])
        if suffix in gold_map:
            return gold_map[suffix]
    return 0.0


def _path_in_set(path: str, gold_set: set[str]) -> bool:
    """True if path matches any gold entry via exact or suffix match."""
    p = path.lower().replace("\\", "/")
    if p in gold_set:
        return True
    parts = p.split("/")
    for n in range(1, len(parts)):
        if "/".join(parts[n:]) in gold_set:
            return True
    return False


def ideal_dcg_graded(gold_paths: list[dict], k: int) -> float:
    """IDCG from graded gold list: sort by relevance desc, take top k."""
    sorted_rels = sorted([g["relevance"] for g in gold_paths], reverse=True)
    return dcg(sorted_rels, k)


def ndcg_graded(retrieved: list[str], gold_paths: list[dict], k: int = 10) -> float:
    """Compute NDCG@k given retrieved path list and graded gold list."""
    if not gold_paths:
        return 0.0
    idcg = ideal_dcg_graded(gold_paths, k)
    if idcg == 0.0:
        return 0.0
    gold_map = {g["path"].lower(): g["relevance"] for g in gold_paths}
    rels = [_path_relevance(p, gold_map) for p in retrieved[:k]]
    return dcg(rels, k) / idcg


def hit_at_k_graded(retrieved: list[str], gold_paths: list[dict], k: int) -> bool:
    """True if any gold path (relevance >= 1) appears in top-k retrieved."""
    gold_set = {g["path"].lower() for g in gold_paths if g.get("relevance", 0) >= 1}
    return any(_path_in_set(p, gold_set) for p in retrieved[:k])


def reciprocal_rank_graded(retrieved: list[str], gold_paths: list[dict], k: int) -> float:
    """Reciprocal rank of first relevant gold path in top-k (0 if none)."""
    gold_set = {g["path"].lower() for g in gold_paths if g.get("relevance", 0) >= 1}
    for i, p in enumerate(retrieved[:k]):
        if _path_in_set(p, gold_set):
            return 1.0 / (i + 1)
    return 0.0
