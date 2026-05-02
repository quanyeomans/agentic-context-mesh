"""usearch-backed VectorRepository implementation.

Wraps VectorIndex behind the VectorRepository protocol so callers depend
on the protocol rather than the concrete index implementation.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kairix.core.search.vec_index import VectorIndex

logger = logging.getLogger(__name__)


class UsearchVectorRepository:
    """VectorRepository implementation backed by usearch HNSW.

    Satisfies kairix.core.protocols.VectorRepository.
    """

    def __init__(self, index: VectorIndex) -> None:
        self._index = index

    def search(
        self,
        query_vec: list[float],
        k: int = 10,
        collections: list[str] | None = None,
    ) -> list[dict]:
        """ANN search with optional collection filtering."""
        import numpy as np

        vec = np.array(query_vec, dtype=np.float32)
        return self._index.search(vec, k=k, collections=collections)

    def add_vectors(self, items: list[tuple[str, list[float]]]) -> int:
        """Add vectors to the index. Returns count added."""
        if not items:
            return 0
        hash_seqs = [h for h, _ in items]
        vectors = [v for _, v in items]
        return self._index.add_vectors(hash_seqs, vectors)

    def count(self) -> int:
        """Return total number of vectors in the index."""
        return len(self._index)
