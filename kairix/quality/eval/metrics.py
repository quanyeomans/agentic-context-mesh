"""Search quality metrics: NDCG, MRR, Hit@K.

Single source of truth for all scoring functions. Supports both binary
relevance (list[str] gold paths) and graded relevance (list[dict] with
{"title": ..., "relevance": 0/1/2} or {"path": ..., "relevance": 0/1/2}).

Title-based matching uses the TREC qrels pattern — stable document identity
via normalised filename stems, robust to vault reorganisation.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Core DCG helpers
# ---------------------------------------------------------------------------


def dcg(relevances: list[float], k: int | None = None) -> float:
    """Discounted Cumulative Gain for a ranked list of relevance scores."""
    items = relevances[:k] if k is not None else relevances
    return sum(rel / math.log2(rank + 2) for rank, rel in enumerate(items))


def ideal_dcg(relevances: list[float], k: int) -> float:
    """Ideal DCG: best possible ordering of relevances up to rank k."""
    ideal = sorted(relevances, reverse=True)[:k]
    return dcg(ideal)


def ideal_dcg_graded(gold: list[dict[str, Any]], k: int) -> float:
    """IDCG from graded gold list: sort by relevance desc, take top k."""
    sorted_rels = sorted([g.get("relevance", 0) for g in gold], reverse=True)
    return dcg(sorted_rels, k)


# Keep underscore aliases for any internal callers
_dcg = dcg
_ideal_dcg = ideal_dcg


# ---------------------------------------------------------------------------
# Unified matching — single implementation for path-suffix AND title-stem
# ---------------------------------------------------------------------------


def _normalise_title(t: str) -> str:
    """Stable document identity key: lowercase, consolidate separators to hyphens."""
    return re.sub(r"[-_\s]+", "-", t.lower()).strip("-")


def _stem_from_path(path: str) -> str:
    """Extract the normalised note-title key from a filesystem path."""
    return _normalise_title(Path(path).stem)


def _path_suffix_normalised(path: str) -> str:
    """Normalise a full path for suffix matching (remove .md, lowercase, hyphens)."""
    return _normalise_title(path.replace(".md", ""))


def match_gold_to_path(gold_ref: str, result_path: str) -> bool:
    """Check if a gold reference (title or path) matches a retrieved path.

    Path-based (contains '/'): match as suffix of the result path.
      gold="engineering/adr-examples/readme" matches
      "reference-library/engineering/adr-examples/readme.md"

    Stem-only (no '/'): match against filename stem only.
      gold="patterns" matches "vault/patterns.md"
    """
    norm_gold = _normalise_title(gold_ref)
    if "/" in gold_ref:
        norm_path = _path_suffix_normalised(result_path)
        return norm_path.endswith(norm_gold)
    return _stem_from_path(result_path) == norm_gold


def relevance_for_path(path: str, gold_list: list[dict[str, Any]]) -> int:
    """Return the relevance grade for a retrieved path against a gold list.

    Supports both {"title": ..., "relevance": N} and {"path": ..., "relevance": N}
    gold entries. Uses dual-mode matching (stem for titles, suffix for paths).

    For path-based gold entries, strips .md extension before matching to align
    with the stem-based convention used by title-based entries.
    """
    for g in gold_list:
        ref = g.get("title") or g.get("path", "")
        if not ref:
            continue
        # Strip .md extension from path-format gold entries
        if ref.endswith(".md"):
            ref = ref[:-3]
        if match_gold_to_path(ref, path):
            return int(g.get("relevance", 0))
    return 0


def _gold_matches_any(gold_list: list[dict[str, Any]], retrieved_path: str) -> bool:
    """True if the retrieved path matches any gold entry with relevance >= 1."""
    for g in gold_list:
        if int(g.get("relevance", 0)) < 1:
            continue
        ref = g.get("title") or g.get("path", "")
        if not ref:
            continue
        if ref.endswith(".md"):
            ref = ref[:-3]
        if match_gold_to_path(ref, retrieved_path):
            return True
    return False


# ---------------------------------------------------------------------------
# Binary-relevance API (gold_paths: list[str]) — original API, preserved
# ---------------------------------------------------------------------------


def ndcg_score(retrieved_paths: list[str], gold_paths: list[str], k: int = 10) -> float:
    """NDCG@k with binary relevance (all gold paths equally relevant)."""
    if not gold_paths:
        return 0.0
    retrieved_paths = list(dict.fromkeys(retrieved_paths))  # dedup, preserve order
    gold_set = {p.lower().replace("\\", "/") for p in gold_paths}
    relevances = [1.0 if p.lower().replace("\\", "/") in gold_set else 0.0 for p in retrieved_paths[:k]]
    ideal_rel = [1.0] * min(len(gold_paths), k)
    idcg = _ideal_dcg(ideal_rel, k)
    if idcg < 1e-9:
        return 0.0
    return _dcg(relevances) / idcg


def hit_at_k(retrieved_paths: list[str], gold_paths: list[str], k: int = 10) -> float:
    """Hit@K: 1.0 if any gold path appears in top-k retrieved, else 0.0."""
    if not gold_paths:
        return 0.0
    gold_set = {p.lower().replace("\\", "/") for p in gold_paths}
    return 1.0 if any(p.lower().replace("\\", "/") in gold_set for p in retrieved_paths[:k]) else 0.0


def mean_reciprocal_rank(retrieved_paths: list[str], gold_paths: list[str]) -> float:
    """MRR: 1/rank of first relevant result (0.0 if none found)."""
    if not gold_paths:
        return 0.0
    gold_set = {p.lower().replace("\\", "/") for p in gold_paths}
    for rank, path in enumerate(retrieved_paths, start=1):
        if path.lower().replace("\\", "/") in gold_set:
            return 1.0 / rank
    return 0.0


# ---------------------------------------------------------------------------
# Graded-relevance API (gold: list[dict[str, Any]])
#
# Accepts both {"title": ..., "relevance": N} and {"path": ..., "relevance": N}.
# Uses match_gold_to_path() for consistent dual-mode matching across all metrics.
# ---------------------------------------------------------------------------


def ndcg_graded(retrieved: list[str], gold: list[dict[str, Any]], k: int = 10) -> float:
    """NDCG@k with graded relevance. Supports title-based and path-based gold entries."""
    if not gold:
        return 0.0
    retrieved = list(dict.fromkeys(retrieved))  # dedup, preserve order
    idcg = ideal_dcg_graded(gold, k)
    if idcg < 1e-9:
        return 0.0
    rels = [float(relevance_for_path(p, gold)) for p in retrieved[:k]]
    return dcg(rels, k) / idcg


def hit_at_k_graded(retrieved: list[str], gold: list[dict[str, Any]], k: int = 5) -> bool:
    """True if any gold entry (relevance >= 1) appears in top-k retrieved."""
    retrieved = list(dict.fromkeys(retrieved))  # dedup, preserve order
    return any(_gold_matches_any(gold, p) for p in retrieved[:k])


def reciprocal_rank_graded(retrieved: list[str], gold: list[dict[str, Any]], k: int = 10) -> float:
    """Reciprocal rank of first relevant gold entry in top-k (0.0 if none)."""
    retrieved = list(dict.fromkeys(retrieved))  # dedup, preserve order
    for i, p in enumerate(retrieved[:k]):
        if _gold_matches_any(gold, p):
            return 1.0 / (i + 1)
    return 0.0
