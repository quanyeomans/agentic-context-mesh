"""Integration tests for the default ChunkerRegistry dispatch table.

ADR-028 Wave G.1 wires the per-type chunkers into
:func:`kairix.core.connectors.chunker_registry.build_default_chunker_registry`.
This test pins the registrations for the THREE plugins this commit
introduces:

  * PPTX → ``SlideChunker``  (sharepoint + google_drive)
  * XLSX/.xls → ``SheetRowChunker``  (sharepoint + google_drive)
  * DOCX → ``DocxHeadingChunker``  (sharepoint + google_drive)

Subsequent waves (3A / 3C) extend the registry with markdown / code /
email / thread / calendar chunkers; this test asserts the
*existence and dispatch* of THIS commit's mappings without making
"only these N mappings exist" claims that would conflict with the
parallel waves.

Sabotage-prove targets:
- Drop a single ``registry.register(...)`` line in
  ``build_default_chunker_registry``: the matching test_dispatches_*
  case fails → restore.

EXECUTED sabotage proof: comment out the
``registry.register(kind="sharepoint", mime=PPTX_MIME, chunker=slide_chunker)``
line; re-run pytest;
test_dispatches_pptx_mime_to_slide_chunker_for_sharepoint fails because
dispatch returns the fallback. Restored.
"""

from __future__ import annotations

import pytest

from kairix.chunkers.docx_heading import DocxHeadingChunker
from kairix.chunkers.sheet_row import SheetRowChunker
from kairix.chunkers.slide import SlideChunker
from kairix.core.connectors.chunker_registry import (
    DOCX_MIME,
    LEGACY_XLS_MIME,
    PPTX_MIME,
    XLSX_MIME,
    build_default_chunker_registry,
)

pytestmark = pytest.mark.integration


def test_dispatches_pptx_mime_to_slide_chunker_for_sharepoint() -> None:
    registry = build_default_chunker_registry()
    chunker = registry.dispatch(kind="sharepoint", mime=PPTX_MIME, section_kind="text")
    assert isinstance(chunker, SlideChunker)


def test_dispatches_pptx_mime_to_slide_chunker_for_google_drive() -> None:
    registry = build_default_chunker_registry()
    chunker = registry.dispatch(kind="google_drive", mime=PPTX_MIME, section_kind="text")
    assert isinstance(chunker, SlideChunker)


def test_dispatches_xlsx_mime_to_sheet_row_chunker_for_sharepoint() -> None:
    registry = build_default_chunker_registry()
    chunker = registry.dispatch(kind="sharepoint", mime=XLSX_MIME, section_kind="tabular")
    assert isinstance(chunker, SheetRowChunker)


def test_dispatches_legacy_xls_mime_to_sheet_row_chunker() -> None:
    registry = build_default_chunker_registry()
    chunker = registry.dispatch(kind="sharepoint", mime=LEGACY_XLS_MIME, section_kind="tabular")
    assert isinstance(chunker, SheetRowChunker)


def test_dispatches_xlsx_mime_to_sheet_row_chunker_for_google_drive() -> None:
    registry = build_default_chunker_registry()
    chunker = registry.dispatch(kind="google_drive", mime=XLSX_MIME, section_kind="tabular")
    assert isinstance(chunker, SheetRowChunker)


def test_dispatches_docx_mime_to_docx_heading_chunker_for_sharepoint() -> None:
    registry = build_default_chunker_registry()
    chunker = registry.dispatch(kind="sharepoint", mime=DOCX_MIME, section_kind="text")
    assert isinstance(chunker, DocxHeadingChunker)


def test_dispatches_docx_mime_to_docx_heading_chunker_for_google_drive() -> None:
    registry = build_default_chunker_registry()
    chunker = registry.dispatch(kind="google_drive", mime=DOCX_MIME, section_kind="text")
    assert isinstance(chunker, DocxHeadingChunker)


def test_unknown_kind_falls_through_to_fallback() -> None:
    """Outside the registered (kind, mime) keys, the fallback runs."""
    registry = build_default_chunker_registry()
    chunker = registry.dispatch(kind="unknown-source", mime=PPTX_MIME, section_kind="text")
    assert chunker is registry.fallback
