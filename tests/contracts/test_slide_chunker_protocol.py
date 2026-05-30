"""Contract tests for :class:`kairix.chunkers.slide.SlideChunker` (F43).

Pins:
* SlideChunker satisfies the :class:`~kairix.core.protocols.Chunker`
  Protocol (structural check).
* SlideChunker declares ``version: str`` (F55 + module-level + instance).
* Every emitted :class:`Chunk` carries ``chunker_version=self.version`` (F55).
* Single-slide input collapses to exactly one Chunk.
* Empty / whitespace-only input emits no chunks.

Sabotage-prove targets:
- chunker_version flow: drop ``chunker_version=self.version`` in
  ``_build_slide_chunk`` → confirm test_chunk_carries_version fails →
  restore.
- Protocol shape: rename ``chunk(...)`` to ``run(...)`` →
  test_satisfies_chunker_protocol fails → restore.
"""

from __future__ import annotations

import pytest

from kairix.chunkers.slide import (
    PLUGIN_NAME,
    SlideChunker,
    make_chunker,
    version,
)
from kairix.core.protocols import Chunk, Chunker

pytestmark = pytest.mark.contract


def test_satisfies_chunker_protocol() -> None:
    chunker = SlideChunker()
    assert isinstance(chunker, Chunker)
    assert chunker.version == version
    assert chunker.name == PLUGIN_NAME


def test_make_chunker_returns_slide_chunker() -> None:
    instance = make_chunker()
    assert isinstance(instance, SlideChunker)


def test_module_version_is_non_empty_string() -> None:
    assert isinstance(version, str)
    assert version.strip() != ""


def test_empty_input_yields_no_chunks() -> None:
    chunker = SlideChunker()
    assert chunker.chunk(text="", section_kind="text", source_uri="x.pptx") == ()
    assert chunker.chunk(text="   \n  ", section_kind="text", source_uri="x.pptx") == ()


def test_single_page_input_collapses_to_one_chunk() -> None:
    """Silver's per-page driver passes one page-text at a time."""
    chunker = SlideChunker()
    text = "Just some slide body text without the header"
    chunks = chunker.chunk(text=text, section_kind="text", source_uri="deck.pptx")
    assert len(chunks) == 1
    assert chunks[0].text == text


def test_chunk_carries_version() -> None:
    """F55: every emitted Chunk carries ``chunker_version=self.version``."""
    chunker = SlideChunker()
    chunks = chunker.chunk(text="slide body", section_kind="text", source_uri="x.pptx")
    assert len(chunks) >= 1
    for c in chunks:
        assert isinstance(c, Chunk)
        assert c.chunker_version == version
        assert c.source_uri == "x.pptx"
