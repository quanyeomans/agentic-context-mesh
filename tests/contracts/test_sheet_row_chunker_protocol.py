"""Contract tests for :class:`kairix.chunkers.sheet_row.SheetRowChunker` (F43).

Pins:
* SheetRowChunker satisfies the :class:`~kairix.core.protocols.Chunker`
  Protocol.
* SheetRowChunker declares ``version: str`` (F55).
* Every emitted :class:`Chunk` carries ``chunker_version=self.version`` (F55).
* Empty / whitespace-only input emits no chunks.
* ``small_sheet_threshold`` is configurable.

Sabotage-prove targets:
- chunker_version flow: drop ``chunker_version=self.version`` in
  ``_build_row_chunk`` → confirm test_chunk_carries_version fails →
  restore.
- Small-sheet branch boundary: change ``len(data_rows) <= threshold``
  to ``len(data_rows) < threshold`` → small-sheet integration test
  fails → restore.
"""

from __future__ import annotations

import pytest

from kairix.chunkers.sheet_row import (
    PLUGIN_NAME,
    SheetRowChunker,
    make_chunker,
    version,
)
from kairix.core.protocols import Chunk, Chunker

pytestmark = pytest.mark.contract


def test_satisfies_chunker_protocol() -> None:
    chunker = SheetRowChunker()
    assert isinstance(chunker, Chunker)
    assert chunker.version == version
    assert chunker.name == PLUGIN_NAME


def test_make_chunker_returns_sheet_row_chunker() -> None:
    instance = make_chunker()
    assert isinstance(instance, SheetRowChunker)


def test_module_version_is_non_empty_string() -> None:
    assert isinstance(version, str)
    assert version.strip() != ""


def test_empty_input_yields_no_chunks() -> None:
    chunker = SheetRowChunker()
    assert chunker.chunk(text="", section_kind="tabular", source_uri="x.xlsx") == ()
    assert chunker.chunk(text="   \n  ", section_kind="tabular", source_uri="x.xlsx") == ()


def test_small_sheet_threshold_is_configurable() -> None:
    chunker = SheetRowChunker(small_sheet_threshold=5)
    assert chunker.small_sheet_threshold == 5


def test_chunk_carries_version() -> None:
    """F55: every emitted Chunk carries ``chunker_version=self.version``."""
    chunker = SheetRowChunker(small_sheet_threshold=0)  # force row-per-chunk
    sheet_text = "## Sheet: TestSheet\n\n| col_a | col_b |\n| --- | --- |\n| v1 | v2 |\n| v3 | v4 |\n"
    chunks = chunker.chunk(text=sheet_text, section_kind="tabular", source_uri="x.xlsx")
    assert len(chunks) == 2
    for c in chunks:
        assert isinstance(c, Chunk)
        assert c.chunker_version == version
        assert c.source_uri == "x.xlsx"
