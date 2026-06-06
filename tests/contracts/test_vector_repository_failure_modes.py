"""F68 (ADR-024 Bundle A) — failure-mode contract for :class:`VectorRepository`.

Three methods on the vector index surface. Each is exercised below
for at least one named failure class:

  * ``search`` — raises on backend failure (vec backend unreachable /
    corrupt) AND returns_empty when the index has no matching vectors
    for the collections filter.
  * ``add_vectors`` — raises when the underlying index rejects the
    batch (typed exception, not silent zero-rows).
  * ``count`` — raises when the backing index handle is unavailable.

The canonical :class:`tests.fakes.FakeVectorRepository` supports a
``raises=`` constructor kwarg flipping ``search`` to raise; a tiny
inline ``_FailingVectorRepository`` exposes the per-method knobs the
canonical fake doesn't.
"""

from __future__ import annotations

from typing import Any

import pytest

from kairix.core.protocols import VectorRepository
from tests.fakes import FakeVectorRepository

pytestmark = pytest.mark.contract


class _FailingVectorRepository:
    """Inline :class:`VectorRepository` with raises-knobs per method."""

    def __init__(
        self,
        *,
        raise_on_search: BaseException | None = None,
        raise_on_add: BaseException | None = None,
        raise_on_count: BaseException | None = None,
    ) -> None:
        self._raise_on_search = raise_on_search
        self._raise_on_add = raise_on_add
        self._raise_on_count = raise_on_count

    def search(
        self,
        query_vec: list[float],
        k: int,
        collections: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        del query_vec, k, collections
        if self._raise_on_search is not None:
            raise self._raise_on_search
        return []

    def add_vectors(self, items: list[tuple[str, list[float]]]) -> int:
        if self._raise_on_add is not None:
            raise self._raise_on_add
        return len(items)

    def count(self) -> int:
        if self._raise_on_count is not None:
            raise self._raise_on_count
        return 0


def test_search_raises_propagates_typed_exception() -> None:
    """A search backend failure surfaces verbatim — callers must not
    silently fall back to BM25-only on a transient vector backend error
    (the caller's job is to retry / classify).

    Sabotage proof: in FakeVectorRepository.search change
    ``raise self._raises`` to ``return []``. Re-run: pytest.raises sees
    no exception. Restored.
    """
    repo = FakeVectorRepository(raises=RuntimeError("F68-vec-raises"))
    with pytest.raises(RuntimeError, match="F68-vec-raises"):
        repo.search(query_vec=[0.1, 0.2], k=5)


def test_search_returns_empty_when_collections_filter_matches_nothing() -> None:
    """Empty result for an unmatched collections filter — callers
    iterate without a None check.

    Sabotage proof: in FakeVectorRepository.search drop the
    collections filter and return all results unconditionally. Re-run:
    the result is non-empty so the ``== []`` assertion fails. Restored.
    """
    repo: VectorRepository = FakeVectorRepository(
        results=[{"path": "a.md", "collection": "alpha"}],
    )
    out = repo.search(query_vec=[0.1, 0.2], k=5, collections=["nope-collection"])
    assert out == [], f"unmatched filter must yield []; got {out!r}"


def test_add_vectors_raises_propagates_typed_exception() -> None:
    """add_vectors raises on backend rejection — caller must NOT
    interpret a swallowed error as "rows written".

    Sabotage proof: change ``_FailingVectorRepository.add_vectors`` to
    ``return 0`` instead of raising. Re-run: pytest.raises sees nothing.
    Restored.
    """
    repo: VectorRepository = _FailingVectorRepository(
        raise_on_add=RuntimeError("F68-add-raises"),
    )
    with pytest.raises(RuntimeError, match="F68-add-raises"):
        repo.add_vectors([("a.md", [0.1, 0.2])])


def test_count_raises_propagates_typed_exception() -> None:
    """count raises when the backing index handle is unavailable —
    operators distinguish "0 vectors" from "index unreachable".

    Sabotage proof: change ``_FailingVectorRepository.count`` to
    ``return 0`` instead of raising. Re-run: pytest.raises sees nothing.
    Restored.
    """
    repo: VectorRepository = _FailingVectorRepository(
        raise_on_count=RuntimeError("F68-count-raises"),
    )
    with pytest.raises(RuntimeError, match="F68-count-raises"):
        repo.count()
