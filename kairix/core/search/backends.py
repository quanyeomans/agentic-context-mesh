"""Adapter classes implementing SearchBackend for BM25 and vector search.

Each backend wraps a protocol implementation (DocumentRepository, VectorRepository,
EmbeddingService) behind a uniform search(query, collections, limit) interface.

These adapters are composed into SearchPipeline — callers never construct them
directly in production code.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kairix.core.protocols import (
        DocumentRepository,
        EmbeddingService,
        VectorRepository,
    )

logger = logging.getLogger(__name__)


class BM25SearchBackend:
    """SearchBackend adapter wrapping BM25 full-text search via DocumentRepository."""

    def __init__(self, doc_repo: DocumentRepository) -> None:
        self._doc_repo = doc_repo

    def search(
        self,
        query: str,
        collections: list[str] | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Delegate to DocumentRepository.search_fts."""
        return self._doc_repo.search_fts(query, collections=collections, limit=limit)


class VectorSearchBackend:
    """SearchBackend adapter wrapping vector search with optional HyDE.

    Embeds the query text via EmbeddingService and searches the VectorRepository.
    When an LLM backend is provided, can apply HyDE (Hypothetical Document
    Embeddings) for semantic/multi_hop intents — not implemented in Phase 4.
    """

    def __init__(
        self,
        embedding: EmbeddingService,
        vector_repo: VectorRepository,
        llm: object | None = None,
    ) -> None:
        self._embedding = embedding
        self._vector_repo = vector_repo
        self._llm = llm  # For HyDE — optional LLMBackend

    def search(
        self,
        query: str,
        collections: list[str] | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """Embed query and run ANN vector search. Returns [] on any failure."""
        try:
            vec = self._embedding.embed(query)
            if not vec:
                return []
            results = self._vector_repo.search(vec, k=limit, collections=collections)
            return results
        except Exception as e:
            logger.warning("VectorSearchBackend.search failed — %s", e)
            return []


class AzureEmbeddingService:
    """EmbeddingService adapter wrapping kairix._azure embed functions.

    Lazily imports kairix._azure to avoid hard dependency at module load.
    Uses existing credential resolution from the Azure module.
    """

    def __init__(self) -> None:
        pass  # Uses existing credential resolution

    def embed(self, text: str) -> list[float]:
        """Embed a single text string. Returns [] on failure."""
        from kairix._azure import embed_text

        return embed_text(text)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts sequentially. Returns list of vectors."""
        from kairix._azure import embed_text

        return [embed_text(t) for t in texts]
