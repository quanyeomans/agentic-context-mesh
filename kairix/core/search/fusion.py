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
    """Reciprocal Rank Fusion strategy (Cormack et al., 2009) with
    asymmetric-list normalisation (Issue #454).

    Merges BM25 and vector result lists using the RRF formula:
      score(d) = sum(w_list * 1 / (k + rank)) across lists
    where ``w_list = len(list) / max(len(bm25), len(vec))``.

    Cormack's original paper assumed symmetric per-system result lists.
    kairix runs asymmetric limits in practice (``RetrievalConfig.defaults()``
    ships ``bm25_limit=20, vec_limit=10``), so without the normalisation
    the longer list silently outweighs the shorter one — every additional
    deeper-rank entry still contributes ``1/(k+rank)``. The per-list
    weight rescales each list's contribution so a unit of rank confidence
    is comparable across the two backends regardless of list length.

    Symmetric inputs (``len(bm25) == len(vec)``) collapse both weights to
    1.0 and the behaviour is bit-identical to classic Cormack 2009 RRF —
    strict no-op for any caller using equal limits.
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
