"""F68 (ADR-024 Bundle A) — failure-mode contract for :class:`DocumentRepository`.

Four Protocol methods: ``search_fts`` / ``get_by_path`` /
``get_chunk_dates`` / ``insert_or_update``.

:class:`tests.fakes.FakeDocumentRepository` supports a ``raises=``
constructor kwarg for the ``search_fts`` failure path. We additionally
probe:

  * ``get_by_path`` returns ``None`` for absent path (the documented
    "no doc" sentinel — callers tolerate missing).
  * ``get_chunk_dates`` returns empty mapping for unknown paths.
  * ``insert_or_update`` raises through to the caller when the
    backend rejects the write (inline raising subclass).

Each test carries a "Sabotage proof:" comment describing the mutation
that proves the assertion has teeth.
"""

from __future__ import annotations

import pytest

from tests.fakes import FakeDocumentRepository

pytestmark = pytest.mark.contract


def test_search_fts_raises_propagates_typed_exception() -> None:
    """A ``DocumentRepository`` whose backend raises (SQLite locked,
    FTS5 corrupt) MUST surface the exception — silent fallback to ``[]``
    would mask infrastructure failure.

    Sabotage proof: in :meth:`FakeDocumentRepository.search_fts` change
    ``raise self._raises`` to ``return []``. Re-run: the test fails
    because no exception fires and the call returns ``[]``. Restored.
    """
    repo = FakeDocumentRepository(documents=[], raises=RuntimeError("F68-fts-locked"))
    with pytest.raises(RuntimeError, match="F68-fts-locked"):
        repo.search_fts("anything")


def test_get_by_path_returns_empty_none_when_path_absent() -> None:
    """``get_by_path`` for an unknown path returns ``None`` — the
    documented "no document" sentinel. Callers distinguish from raised
    exception.

    Sabotage proof: in :meth:`FakeDocumentRepository.get_by_path`
    change ``return self._docs.get(path)`` to
    ``return {"path": path, "title": "ghost"}``. Re-run: the test
    fails because the call returns a dict instead of ``None``.
    Restored.
    """
    repo = FakeDocumentRepository(documents=[{"path": "a.md", "title": "alpha", "content": "body"}])
    assert repo.get_by_path("missing.md") is None


def test_get_chunk_dates_returns_empty_when_no_paths_match() -> None:
    """``get_chunk_dates`` for unknown paths returns an empty mapping
    — callers use this to skip date-boost when no chunk dates exist.

    Sabotage proof: in :meth:`FakeDocumentRepository.get_chunk_dates`
    change the final ``return result`` to
    ``return {"ghost.md": "2026-01-01"}``. Re-run: the test fails
    because the result has a ghost entry. Restored.
    """
    repo = FakeDocumentRepository(documents=[])
    assert repo.get_chunk_dates(["missing-a.md", "missing-b.md"]) == {}


def test_insert_or_update_raises_when_backend_rejects() -> None:
    """A repo whose ``insert_or_update`` raises must surface — silent
    failure would let the caller's "wrote N docs" report lie.

    Sabotage proof: in ``_RaisingRepo.insert_or_update`` change
    ``raise self._exc`` to ``return None``. Re-run: the test fails
    because no exception fires. Restored.
    """

    class _RaisingRepo(FakeDocumentRepository):
        def insert_or_update(
            self,
            path: str,
            collection: str,
            title: str,
            content: str,
            content_hash: str,
        ) -> None:
            del path, collection, title, content, content_hash
            raise RuntimeError("F68-insert-rejected")

    with pytest.raises(RuntimeError, match="F68-insert-rejected"):
        _RaisingRepo().insert_or_update("a.md", "default", "t", "c", "h")
