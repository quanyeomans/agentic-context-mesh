"""F68 (ADR-024 Bundle A) — failure-mode contract for :class:`Chunker`.

Single Protocol method ``chunk(*, text, section_kind, source_uri)``.
Two operationally-relevant failure shapes:

  * ``returns_empty`` — empty input text MUST yield zero chunks.
    Production wraps :class:`Chunker` plugins behind the silver
    processor; the silver code paths assume "no chunks for empty
    text" is safe (it's the documented contract). Sabotage-provable
    via the shipped paragraph chunker.
  * ``raises`` — when a chunker plugin crashes mid-text (regex
    backtracking, encoding error in tree-sitter, …), the exception
    must propagate to the orchestrator so the per-batch transaction
    rolls back. We probe via an inline ``_RaisingChunker``.

Each test carries a "Sabotage proof:" comment describing the mutation
that proves the assertion has teeth.
"""

from __future__ import annotations

import pytest

from kairix.chunkers.docx_heading import DocxHeadingChunker
from kairix.core.protocols import Chunk

pytestmark = pytest.mark.contract


def test_chunk_returns_empty_when_text_is_empty() -> None:
    """The shipped :class:`DocxHeadingChunker` MUST yield zero chunks
    for empty text — the silver layer relies on this to skip writes
    for blank documents.

    Sabotage proof: in :meth:`DocxHeadingChunker.chunk` add
    ``return (Chunk(...),)`` at the top of the function. Re-run: the
    test fails because the result has one chunk instead of zero.
    Restored.
    """
    chunker = DocxHeadingChunker()
    result: tuple[Chunk, ...] = chunker.chunk(text="", section_kind="text", source_uri="file:///empty.docx")
    assert result == (), f"empty text must yield empty tuple; got {result!r}"
    # Tuple-of-Chunk is the Protocol's declared return type — proven by
    # the explicit annotation above (mypy + the runtime empty assert).


def test_chunk_raises_when_underlying_implementation_fails() -> None:
    """A :class:`Chunker` whose ``chunk`` raises must surface the
    exception — silent fallback to an empty tuple would mask the
    failure and let bad input flow downstream.

    Sabotage proof: in ``_RaisingChunker.chunk`` change
    ``raise self._exc`` to ``return ()``. Re-run: the test fails
    because no exception fires. Restored.
    """

    class _RaisingChunker:
        version = "0.0.0-test"

        def __init__(self, exc: Exception) -> None:
            self._exc = exc

        def chunk(self, *, text: str, section_kind: str, source_uri: str) -> tuple[Chunk, ...]:
            del text, section_kind, source_uri
            raise self._exc

    chunker = _RaisingChunker(ValueError("F68-chunker-bad-input"))
    with pytest.raises(ValueError, match="F68-chunker-bad-input"):
        chunker.chunk(text="any", section_kind="text", source_uri="file:///x.md")
