"""kairix.contracts.embed — Embedder Protocol definition."""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbedderProtocol(Protocol):
    """Protocol for any embedding backend used in kairix."""

    def embed(self, text: str) -> list[float]:
        """Return a float vector for the given text."""
        ...

    def embed_as_bytes(self, text: str) -> bytes | None:
        """Return the embedding packed as raw bytes (float32 little-endian), or None."""
        ...

    def dimension(self) -> int:
        """Return the dimensionality of vectors produced by this embedder."""
        ...
