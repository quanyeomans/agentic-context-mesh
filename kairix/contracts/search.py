"""kairix.contracts.search — Search domain Protocol definitions."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SearchResultProtocol(Protocol):
    """Protocol for a single search result returned by any search backend."""

    path: str
    score: float
    title: str
    snippet: str
    intent: str


@runtime_checkable
class SearchBackendProtocol(Protocol):
    """Protocol for a search backend that accepts a query and returns results."""

    def search(
        self,
        query: str,
        agent: str | None = None,
        limit: int = 10,
    ) -> list[SearchResultProtocol]:
        """Run a search query and return a ranked list of results."""
        ...
