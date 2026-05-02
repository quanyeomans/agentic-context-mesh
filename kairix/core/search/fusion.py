"""
Strategy pattern implementations for result fusion.

Wraps existing rrf() and bm25_primary_fuse() functions as FusionStrategy
protocol implementations. No logic duplication — delegates to the existing
functions in kairix.core.search.rrf.
"""

from __future__ import annotations

from kairix.core.search.rrf import bm25_primary_fuse as _bm25_primary_fn
from kairix.core.search.rrf import rrf as _rrf_fn


class RRFFusion:
    """Reciprocal Rank Fusion strategy (Cormack et al., 2009).

    Merges BM25 and vector result lists with equal weight using the standard
    RRF formula: score(d) = sum(1 / (k + rank)) across lists.
    """

    def __init__(self, k: int = 60) -> None:
        self._k = k

    def fuse(self, bm25: list, vec: list) -> list:
        return _rrf_fn(bm25, vec, k=self._k)


class BM25PrimaryFusion:
    """BM25-primary fusion: BM25 results first, vector-only appended.

    Preserves BM25 ranking order while gaining vector recall. Use for
    structured knowledge bases where filenames and headings carry strong signal.
    """

    def fuse(self, bm25: list, vec: list) -> list:
        return _bm25_primary_fn(bm25, vec)
