"""Step definitions for ``chunker_sheet_row.feature`` (ADR-028 Wave G.1).

Drives the real :class:`kairix.chunkers.sheet_row.SheetRowChunker`
directly. Scripted sheet markdown matches the shape XlsxExtractor
produces (``## Sheet: <title>`` + pipe-syntax table) so the chunker
runs production split logic on production-shaped input.

Sabotage-proofs per step:
  * "emits one chunk per data row" — flipping ``len(data_rows) <=
    threshold`` to ``True`` (always small-sheet) fails the step.
  * "each chunk text starts with the header row" — dropping the
    header prepend in :func:`_build_row_chunk` fails the step.
  * "emits exactly one chunk for the whole sheet" — flipping the
    small-sheet branch to per-row fails the step.
"""

from __future__ import annotations

from typing import Any

import pytest
from pytest_bdd import given, then, when

from kairix.chunkers.sheet_row import SheetRowChunker

pytestmark = pytest.mark.bdd


_BIG_SHEET_HEADER = "| invoice_id | customer | period |"


@pytest.fixture
def sheet_row_state() -> dict[str, Any]:
    return {
        "chunker": None,
        "sheet_markdown": "",
        "chunks": (),
    }


def _hundred_row_sheet() -> str:
    separator = "| --- | --- | --- |"
    rows = [f"| INV-{i:04d} | client-{i % 7} | 2026-{((i % 12) + 1):02d} |" for i in range(1, 101)]
    return "## Sheet: FY26-Forecast\n\n" + "\n".join([_BIG_SHEET_HEADER, separator, *rows])


def _twenty_row_sheet() -> str:
    header = "| code | label |"
    separator = "| --- | --- |"
    rows = [f"| C{i:02d} | label-{i} |" for i in range(1, 21)]
    return "## Sheet: StatusCodes\n\n" + "\n".join([header, separator, *rows])


@given("the sheet row chunker is constructed with the default threshold")
def _construct_sheet_row(sheet_row_state: dict[str, Any]) -> None:
    sheet_row_state["chunker"] = SheetRowChunker()


@given("the operator has a scripted sheet with one hundred data rows")
def _hundred_row(sheet_row_state: dict[str, Any]) -> None:
    sheet_row_state["sheet_markdown"] = _hundred_row_sheet()


@given("the operator has a scripted sheet with twenty data rows")
def _twenty_row(sheet_row_state: dict[str, Any]) -> None:
    sheet_row_state["sheet_markdown"] = _twenty_row_sheet()


@when("the operator invokes the sheet row chunker on the sheet markdown")
def _invoke_sheet_row(sheet_row_state: dict[str, Any]) -> None:
    chunker: SheetRowChunker = sheet_row_state["chunker"]
    sheet_row_state["chunks"] = chunker.chunk(
        text=sheet_row_state["sheet_markdown"],
        section_kind="tabular",
        source_uri="agent-alpha-sheet.xlsx",
    )


@then("the sheet row chunker emits one chunk per data row")
def _one_chunk_per_row(sheet_row_state: dict[str, Any]) -> None:
    assert len(sheet_row_state["chunks"]) == 100


@then("each chunk text starts with the header row")
def _starts_with_header(sheet_row_state: dict[str, Any]) -> None:
    for c in sheet_row_state["chunks"]:
        assert c.text.split("\n", 1)[0] == _BIG_SHEET_HEADER


@then("the sheet row chunker emits exactly one chunk for the whole sheet")
def _one_chunk_whole_sheet(sheet_row_state: dict[str, Any]) -> None:
    assert len(sheet_row_state["chunks"]) == 1
    only = sheet_row_state["chunks"][0]
    # The whole-sheet text retains the markdown.
    assert "## Sheet: StatusCodes" in only.text
    assert "| C20 | label-20 |" in only.text
