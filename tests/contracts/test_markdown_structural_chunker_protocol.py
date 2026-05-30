"""Contract tests for :class:`MarkdownStructuralChunker` (ADR-028 Wave G.1).

Pins:
  * Plugin instance satisfies the
    :class:`kairix.core.protocols.Chunker` runtime-checkable Protocol.
  * Plugin declares non-empty ``version`` + ``name`` attributes (F55).
  * Every emitted :class:`Chunk` carries ``chunker_version=`` matching
    the plugin instance's version (F55).
  * Empty / whitespace-only input emits no chunks.
  * Single-line markdown with no headings still emits one chunk that
    carries the plugin's chunker_version.
  * The ``make_chunker`` factory returns an instance whose
    ``version`` matches the module-level ``version`` constant.

Sabotage proofs (executed inline where called out):
  * F55 carry-through: change ``_build_chunk`` to drop the
    ``chunker_version=`` kwarg → ``test_emitted_chunks_carry_plugin_version``
    fails. Executed below as a unit test that mutates a Chunk
    constructed without chunker_version and asserts the assertion
    fires.
  * Protocol shape: change ``MarkdownStructuralChunker.chunk`` to
    return a list instead of tuple → isinstance(Chunker) still
    passes (Protocol is structural) but the silver consumer breaks.
    Asserted separately.
"""

from __future__ import annotations

import pytest

from kairix.chunkers.markdown_structural import (
    MarkdownStructuralChunker,
    make_chunker,
    version,
)
from kairix.core.protocols import Chunk, Chunker

pytestmark = pytest.mark.contract


def test_plugin_satisfies_chunker_protocol() -> None:
    chunker = make_chunker()
    assert isinstance(chunker, Chunker)
    assert chunker.name == "markdown_structural"
    assert chunker.version == version
    assert chunker.version  # non-empty (F55)


def test_factory_returns_real_class() -> None:
    chunker = make_chunker()
    assert isinstance(chunker, MarkdownStructuralChunker)


def test_emitted_chunks_carry_plugin_version() -> None:
    """Every Chunk emitted carries chunker_version=self.version (F55).

    Sabotage-proof executed: a Chunk constructed without
    chunker_version (None default) trips the same assertion below,
    proving the assertion has bite.
    """
    chunker = MarkdownStructuralChunker(version="abc-7")
    text = "# Heading\n\nParagraph body with content.\n"
    chunks = chunker.chunk(text=text, section_kind="text", source_uri="doc.md")
    assert chunks
    for chunk in chunks:
        assert isinstance(chunk, Chunk)
        assert chunk.chunker_version == "abc-7"

    # Sabotage executed inline: a Chunk built directly without
    # chunker_version flunks the same loop, confirming the assertion
    # would fail if the plugin stopped threading the version through.
    sabotaged = Chunk(
        text="x",
        content_hash="h",
        source_name="",
        source_uri="doc.md",
        source_modified_at="",
        source_page=None,
        sensitivity="internal",
    )
    assert sabotaged.chunker_version != "abc-7"


def test_empty_input_emits_no_chunks() -> None:
    chunker = make_chunker()
    assert chunker.chunk(text="", section_kind="text", source_uri="x") == ()
    assert chunker.chunk(text="   \n   ", section_kind="text", source_uri="x") == ()


def test_single_paragraph_emits_one_chunk() -> None:
    chunker = make_chunker()
    chunks = chunker.chunk(text="just a body line", section_kind="text", source_uri="x")
    assert len(chunks) == 1
    assert chunks[0].chunker_version == version
    assert "just a body line" in chunks[0].text


def test_emitted_chunks_propagate_source_uri() -> None:
    """F39 / Protocol contract: source_uri flows through to every emitted Chunk."""
    chunker = make_chunker()
    chunks = chunker.chunk(text="# Title\n\nBody", section_kind="text", source_uri="vault/note.md")
    assert chunks
    for chunk in chunks:
        assert chunk.source_uri == "vault/note.md"


def test_chunk_method_returns_tuple_not_list() -> None:
    """The Chunker Protocol promises ``tuple[Chunk, ...]`` — Silver
    relies on the tuple shape (frozen / hashable). A list return
    would type-check but break consumers iterating multiple times.
    """
    chunker = make_chunker()
    result = chunker.chunk(text="# T\n\nbody", section_kind="text", source_uri="x")
    assert isinstance(result, tuple)
