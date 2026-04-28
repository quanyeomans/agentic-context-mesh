"""Search quality metrics: NDCG, MRR, Hit@K.

These are extracted from kairix.quality.benchmark.runner and exposed here so that
both the benchmark runner and the eval reporter can share the same implementations.
"""

from __future__ import annotations

import math


def _dcg(relevances: list[float]) -> float:
    """Discounted Cumulative Gain for a ranked list of relevance scores."""
    return sum(rel / math.log2(rank + 2) for rank, rel in enumerate(relevances))


def _ideal_dcg(relevances: list[float], k: int) -> float:
    """Ideal DCG: best possible ordering of relevances up to rank k."""
    ideal = sorted(relevances, reverse=True)[:k]
    return _dcg(ideal)


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
