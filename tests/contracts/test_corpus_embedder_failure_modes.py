"""F68 (ADR-024 Bundle A) — failure-mode contract for :class:`CorpusEmbedder`.

Single Protocol method ``embed(paths_to_embed)``. The docstring pins
the failure surface: an empty tuple is a legal no-op signal (returns
``0`` chunks indexed). A raise from the underlying embed pipeline
must propagate.

We probe both the ``returns_empty`` shape via
:class:`tests.fakes.FakeCorpusEmbedder` and the ``raises`` shape via
an inline ``_RaisingEmbedder``.

Each test carries a "Sabotage proof:" comment describing the mutation
that proves the assertion has teeth.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.fakes import FakeCorpusEmbedder

pytestmark = pytest.mark.contract


def test_embed_returns_empty_count_when_paths_empty() -> None:
    """``embed(())`` is the documented no-op signal; the embedder MUST
    return ``0`` chunks indexed (not raise on the empty tuple, not
    invent ghost chunks).

    Sabotage proof: in :meth:`FakeCorpusEmbedder.embed` change
    ``return 0`` (in the empty-scripted branch) to ``return 99``.
    Re-run: the test fails because the call returns 99 instead of 0.
    Restored.
    """
    embedder = FakeCorpusEmbedder()
    chunks_indexed = embedder.embed(paths_to_embed=())
    assert chunks_indexed == 0, f"empty paths must return 0; got {chunks_indexed}"
    # And the empty call STILL lands in calls — proves the embedder
    # received the empty tuple rather than skipping it entirely.
    assert embedder.calls == [()], f"calls must record empty tuple; got {embedder.calls!r}"


def test_embed_raises_when_underlying_pipeline_crashes() -> None:
    """An embedder whose ``embed`` raises must surface the exception
    — silent fallback to ``0`` would mask broken embedding state and
    let downstream ``IngestResult.chunks_indexed`` lie about coverage.

    Sabotage proof: in ``_RaisingEmbedder.embed`` change
    ``raise self._exc`` to ``return 0``. Re-run: the test fails because
    no exception fires. Restored.
    """

    class _RaisingEmbedder:
        def __init__(self, exc: Exception) -> None:
            self._exc = exc

        def embed(self, paths_to_embed: tuple[Path, ...]) -> int:
            del paths_to_embed
            raise self._exc

    embedder = _RaisingEmbedder(RuntimeError("F68-embedder-cuda-oom"))
    with pytest.raises(RuntimeError, match="F68-embedder-cuda-oom"):
        embedder.embed(paths_to_embed=(Path("/fake/doc.md"),))
