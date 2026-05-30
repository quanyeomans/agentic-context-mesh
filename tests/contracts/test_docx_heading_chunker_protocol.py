"""Contract tests for :class:`kairix.chunkers.docx_heading.DocxHeadingChunker` (F43).

Pins:
* DocxHeadingChunker satisfies the
  :class:`~kairix.core.protocols.Chunker` Protocol.
* DocxHeadingChunker declares ``version: str`` (F55).
* Every emitted :class:`Chunk` carries ``chunker_version=self.version`` (F55).
* Empty / whitespace-only input emits no chunks.

Sabotage-prove targets:
- chunker_version flow: drop ``chunker_version=self.version`` in
  ``_build_section_chunk`` → confirm test_chunk_carries_version fails →
  restore.
- Heading-split shape: change ``_HEADING_RE`` to match only ``#``
  (not ``#{1,6}``) → integration test for H2/H3 section boundaries
  fails → restore.
"""

from __future__ import annotations

import pytest

from kairix.chunkers.docx_heading import (
    PLUGIN_NAME,
    DocxHeadingChunker,
    make_chunker,
    version,
)
from kairix.core.protocols import Chunk, Chunker

pytestmark = pytest.mark.contract


def test_satisfies_chunker_protocol() -> None:
    chunker = DocxHeadingChunker()
    assert isinstance(chunker, Chunker)
    assert chunker.version == version
    assert chunker.name == PLUGIN_NAME


def test_make_chunker_returns_docx_heading_chunker() -> None:
    instance = make_chunker()
    assert isinstance(instance, DocxHeadingChunker)


def test_module_version_is_non_empty_string() -> None:
    assert isinstance(version, str)
    assert version.strip() != ""


def test_empty_input_yields_no_chunks() -> None:
    chunker = DocxHeadingChunker()
    assert chunker.chunk(text="", section_kind="text", source_uri="x.docx") == ()
    assert chunker.chunk(text="   \n  ", section_kind="text", source_uri="x.docx") == ()


def test_chunk_carries_version() -> None:
    """F55: every emitted Chunk carries ``chunker_version=self.version``."""
    chunker = DocxHeadingChunker()
    text = "# Chapter\n\nFirst paragraph of the chapter."
    chunks = chunker.chunk(text=text, section_kind="text", source_uri="x.docx")
    assert len(chunks) >= 1
    for c in chunks:
        assert isinstance(c, Chunk)
        assert c.chunker_version == version
        assert c.source_uri == "x.docx"
