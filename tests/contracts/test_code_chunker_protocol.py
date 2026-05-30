"""Contract tests for :class:`CodeChunker` (ADR-028 Wave G.1).

Pins:
  * Plugin instance satisfies the
    :class:`kairix.core.protocols.Chunker` runtime-checkable Protocol.
  * Plugin declares non-empty ``version`` + ``name`` attributes (F55).
  * The ``language`` argument selects the separator stack — unknown
    languages fall back to a generic stack without raising.
  * Every emitted :class:`Chunk` carries ``chunker_version=`` matching
    the plugin instance's version (F55).
  * Empty / whitespace-only input emits no chunks.

Sabotage proofs (executed inline):
  * F55 carry-through: a Chunk constructed without chunker_version
    proves the assertion in
    ``test_emitted_chunks_carry_plugin_version`` would fail if the
    plugin stopped threading the version through.
"""

from __future__ import annotations

import pytest

from kairix.chunkers.code import CodeChunker, make_chunker, version
from kairix.core.protocols import Chunk, Chunker

pytestmark = pytest.mark.contract


def test_plugin_satisfies_chunker_protocol() -> None:
    chunker = make_chunker(language="python")
    assert isinstance(chunker, Chunker)
    assert chunker.name == "code"
    assert chunker.version == version
    assert chunker.version  # non-empty (F55)


def test_factory_returns_real_class() -> None:
    chunker = make_chunker(language="python")
    assert isinstance(chunker, CodeChunker)


@pytest.mark.parametrize("language", ["python", "go", "typescript", "unknown_lang"])
def test_unknown_language_falls_back_without_raising(language: str) -> None:
    """Unknown languages get the generic separator stack — never raise."""
    chunker = make_chunker(language=language)
    assert chunker.language == language


def test_emitted_chunks_carry_plugin_version() -> None:
    """Every Chunk carries chunker_version=self.version (F55).

    Sabotage-proof executed inline: a Chunk built without
    chunker_version trips the assertion shape, proving the test bites.
    """
    chunker = CodeChunker(language="python", version="code-v9")
    text = "class Foo:\n    def bar(self):\n        return 1\n"
    chunks = chunker.chunk(text=text, section_kind="text", source_uri="src/foo.py")
    assert chunks
    for chunk in chunks:
        assert isinstance(chunk, Chunk)
        assert chunk.chunker_version == "code-v9"

    sabotaged = Chunk(
        text="y",
        content_hash="h",
        source_name="",
        source_uri="src/foo.py",
        source_modified_at="",
        source_page=None,
        sensitivity="internal",
    )
    assert sabotaged.chunker_version != "code-v9"


def test_empty_input_emits_no_chunks() -> None:
    chunker = make_chunker(language="python")
    assert chunker.chunk(text="", section_kind="text", source_uri="x") == ()
    assert chunker.chunk(text="   \n  \n  ", section_kind="text", source_uri="x") == ()


def test_emitted_chunks_propagate_source_uri() -> None:
    """source_uri flows through every emitted Chunk per F39 / Protocol."""
    chunker = make_chunker(language="python")
    chunks = chunker.chunk(text="def foo():\n    return 1\n", section_kind="text", source_uri="src/x.py")
    assert chunks
    for chunk in chunks:
        assert chunk.source_uri == "src/x.py"


def test_language_surfaced_in_metadata() -> None:
    """Each Chunk's metadata carries the configured language so downstream
    retrieval can render syntax-aware previews.
    """
    chunker = make_chunker(language="python")
    chunks = chunker.chunk(text="def foo():\n    pass\n", section_kind="text", source_uri="x.py")
    assert chunks
    for chunk in chunks:
        assert chunk.metadata.get("language") == "python"


def test_chunk_method_returns_tuple_not_list() -> None:
    chunker = make_chunker(language="python")
    assert isinstance(
        chunker.chunk(text="def x():\n    pass\n", section_kind="text", source_uri="x"),
        tuple,
    )
