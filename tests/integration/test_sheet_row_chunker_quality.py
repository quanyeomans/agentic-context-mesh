"""Quality integration tests for :class:`SheetRowChunker` (ADR-028 Wave G.1).

Two scenarios from ADR-028 §"XLSX — SheetRowChunker":
  * **Large sheet (>= threshold rows).** Every data row becomes one
    chunk with the header row prepended. Metadata carries
    ``sheet_name`` + ``row_index`` + ``cell_ref`` for retrieval
    traceability.
  * **Small reference sheet (<= threshold rows).** The whole sheet
    collapses to one chunk so the inter-row context (e.g. a 20-row
    enum table) is preserved.

Sabotage-prove targets:
- Drop the header-prepend in ``_build_row_chunk``: large-sheet
  test_every_chunk_starts_with_header fails → restore.
- Flip the small-sheet branch (always row-per-chunk):
  test_small_sheet_collapses_to_one_chunk fails → restore.
- Strip the ``cell_ref`` metadata write:
  test_metadata_carries_traceability fails → restore.

EXECUTED sabotage proof: edit the chunk_text in ``_build_row_chunk``
to ``row_line`` only (drop header + separator) and re-run pytest;
test_every_chunk_starts_with_header reports missing header on every
chunk. Restored.
"""

from __future__ import annotations

import pytest

from kairix.chunkers.sheet_row import SheetRowChunker, version

pytestmark = pytest.mark.integration


def _hundred_row_sheet_markdown() -> str:
    """Return a XlsxExtractor-shaped 100-data-row sheet."""
    header = "| invoice_id | customer | period | total_aud |"
    separator = "| --- | --- | --- | --- |"
    rows = [f"| INV-{i:04d} | client-{i % 7} | 2026-{((i % 12) + 1):02d} | {1000 + i} |" for i in range(1, 101)]
    return "## Sheet: FY26-Forecast\n\n" + "\n".join([header, separator, *rows])


def _twenty_row_sheet_markdown() -> str:
    """Return a XlsxExtractor-shaped 20-data-row small reference sheet."""
    header = "| code | label |"
    separator = "| --- | --- |"
    rows = [f"| C{i:02d} | label-{i} |" for i in range(1, 21)]
    return "## Sheet: StatusCodes\n\n" + "\n".join([header, separator, *rows])


def test_large_sheet_emits_one_chunk_per_data_row() -> None:
    """100 data rows in → exactly 100 chunks out (above the 50 threshold)."""
    chunker = SheetRowChunker()
    chunks = chunker.chunk(
        text=_hundred_row_sheet_markdown(),
        section_kind="tabular",
        source_uri="agent-alpha-forecast.xlsx",
    )
    assert len(chunks) == 100


def test_every_chunk_starts_with_header() -> None:
    """ADR-028: header row prepended to each row chunk."""
    chunker = SheetRowChunker()
    chunks = chunker.chunk(
        text=_hundred_row_sheet_markdown(),
        section_kind="tabular",
        source_uri="agent-alpha-forecast.xlsx",
    )
    header_line = "| invoice_id | customer | period | total_aud |"
    for c in chunks:
        # The header is the first line of the chunk text.
        first_line = c.text.split("\n", 1)[0]
        assert first_line == header_line


def test_metadata_carries_traceability_for_each_row() -> None:
    """Each chunk carries ``sheet_name`` + ``row_index`` + ``cell_ref``."""
    chunker = SheetRowChunker()
    chunks = chunker.chunk(
        text=_hundred_row_sheet_markdown(),
        section_kind="tabular",
        source_uri="agent-alpha-forecast.xlsx",
    )
    assert all(c.metadata["sheet_name"] == "FY26-Forecast" for c in chunks)
    # row_index is 1-indexed within the data block (separate from
    # spreadsheet's A1-anchored cell ref).
    assert chunks[0].metadata["row_index"] == "1"
    assert chunks[-1].metadata["row_index"] == "100"
    # cell_ref is the A1-style ref of the row's first cell;
    # spreadsheet row 1 is the header, so data row N is at A(N+1).
    assert chunks[0].metadata["cell_ref"] == "A2"
    assert chunks[99].metadata["cell_ref"] == "A101"


def test_small_sheet_collapses_to_one_chunk() -> None:
    """20-row sheet (<= 50 threshold) → exactly one whole-sheet chunk."""
    chunker = SheetRowChunker()
    chunks = chunker.chunk(
        text=_twenty_row_sheet_markdown(),
        section_kind="tabular",
        source_uri="agent-alpha-statuscodes.xlsx",
    )
    assert len(chunks) == 1
    only = chunks[0]
    # Whole sheet is preserved: header + every row line shows up in the chunk.
    for i in range(1, 21):
        assert f"| C{i:02d} | label-{i} |" in only.text
    assert only.metadata["sheet_name"] == "StatusCodes"
    assert only.metadata["row_index"] == "0"
    assert only.metadata["cell_ref"] == "A1"


def test_threshold_boundary_is_inclusive() -> None:
    """At threshold = N exactly: still small-sheet branch (<=, not <)."""
    chunker = SheetRowChunker(small_sheet_threshold=20)
    chunks = chunker.chunk(
        text=_twenty_row_sheet_markdown(),
        section_kind="tabular",
        source_uri="agent-alpha-statuscodes.xlsx",
    )
    assert len(chunks) == 1


def test_below_threshold_emits_per_row_chunks() -> None:
    """Lower threshold to 19: 20 rows > threshold → per-row chunks."""
    chunker = SheetRowChunker(small_sheet_threshold=19)
    chunks = chunker.chunk(
        text=_twenty_row_sheet_markdown(),
        section_kind="tabular",
        source_uri="agent-alpha-statuscodes.xlsx",
    )
    assert len(chunks) == 20


def test_chunker_version_flows_through() -> None:
    """F55 propagation — sanity-check across both branches."""
    chunker = SheetRowChunker()
    big = chunker.chunk(
        text=_hundred_row_sheet_markdown(),
        section_kind="tabular",
        source_uri="agent-alpha-forecast.xlsx",
    )
    small = chunker.chunk(
        text=_twenty_row_sheet_markdown(),
        section_kind="tabular",
        source_uri="agent-alpha-statuscodes.xlsx",
    )
    for c in (*big, *small):
        assert c.chunker_version == version
