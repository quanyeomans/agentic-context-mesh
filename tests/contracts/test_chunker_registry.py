"""Contract tests for ChunkerRegistry (ADR v2 §6 per-(kind, mime) dispatch).

Pins:
* Exact ``(kind, mime)`` match returns the registered chunker.
* Miss returns the fallback (paragraph-shaped) chunker.
* The fallback declares ``version: str = "1"`` (F55).
* Every Chunk emitted by the fallback carries ``chunker_version="1"`` (F55).
* ``Chunker`` Protocol is satisfied by the fallback.
* Dispatch is deterministic — same key always picks the same chunker.

Sabotage-prove targets:
- Dispatch determinism: change ``return self._registry.get((kind, mime),
  self._fallback)`` to ``return self._fallback`` → confirm
  test_dispatch_picks_registered_chunker fails → restore.
- F55 chunker_version flow: change ``ParagraphFallbackChunker.chunk``
  to omit ``chunker_version=self.version`` → confirm
  test_fallback_chunks_carry_chunker_version fails → restore.
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
    ChunkerRegistry,
    ParagraphFallbackChunker,
    build_default_chunker_registry,
)
from kairix.core.protocols import Chunk, Chunker

pytestmark = pytest.mark.contract


def test_fallback_satisfies_chunker_protocol() -> None:
    fallback = ParagraphFallbackChunker(version="1")
    assert isinstance(fallback, Chunker)
    assert fallback.version == "1"


def test_fallback_chunks_carry_chunker_version() -> None:
    """Every Chunk emitted by the fallback carries chunker_version=self.version."""
    fallback = ParagraphFallbackChunker(version="7")
    text = "first paragraph\n\nsecond paragraph\n\nthird"
    chunks = fallback.chunk(text=text, section_kind="text", source_uri="sample.md")
    assert chunks  # the fallback emits at least one chunk on a non-empty input
    for c in chunks:
        assert isinstance(c, Chunk)
        assert c.chunker_version == "7"
        assert c.source_uri == "sample.md"


def test_fallback_empty_input_yields_no_chunks() -> None:
    fallback = ParagraphFallbackChunker(version="1")
    assert fallback.chunk(text="", section_kind="text", source_uri="x") == ()
    assert fallback.chunk(text="   \n   ", section_kind="text", source_uri="x") == ()


def test_dispatch_picks_registered_chunker() -> None:
    """register((kind, mime), chunker) → dispatch returns it on exact key."""
    registry = ChunkerRegistry()

    class _ScriptedChunker:
        version = "scripted-v1"

        def chunk(self, *, text: str, section_kind: str, source_uri: str) -> tuple[Chunk, ...]:
            del text, section_kind
            return (
                Chunk(
                    text="scripted",
                    content_hash="h",
                    source_name="src",
                    source_uri=source_uri,
                    source_modified_at="2026-05-23T00:00:00Z",
                    source_page=None,
                    sensitivity="internal",
                    chunker_version=self.version,
                ),
            )

    chunker = _ScriptedChunker()
    registry.register(kind="code", mime="text/x-python", chunker=chunker)
    picked = registry.dispatch(kind="code", mime="text/x-python", section_kind="text")
    assert picked is chunker


def test_dispatch_misses_fall_through_to_fallback() -> None:
    registry = ChunkerRegistry()
    picked = registry.dispatch(kind="unknown", mime="text/plain", section_kind="text")
    assert picked is registry.fallback


def test_dispatch_is_deterministic() -> None:
    """Same (kind, mime) always resolves to the same chunker."""
    registry = ChunkerRegistry()
    first = registry.dispatch(kind="code", mime="text/x-python", section_kind="text")
    second = registry.dispatch(kind="code", mime="text/x-python", section_kind="text")
    assert first is second
    third = registry.dispatch(kind="ticketing", mime="application/json", section_kind="text")
    assert third is first  # both miss → fallback singleton


def test_registered_keys_snapshot() -> None:
    registry = ChunkerRegistry()
    assert registry.registered_keys() == ()

    class _Dummy:
        version = "1"

        def chunk(self, *, text: str, section_kind: str, source_uri: str) -> tuple[Chunk, ...]:
            del text, section_kind, source_uri
            return ()

    registry.register(kind="a", mime="b", chunker=_Dummy())
    registry.register(kind="x", mime="y", chunker=_Dummy())
    assert registry.registered_keys() == (("a", "b"), ("x", "y"))


def test_build_default_chunker_registry_returns_chunker_registry() -> None:
    """ADR-028 Wave G.1 factory returns a populated ChunkerRegistry."""
    registry = build_default_chunker_registry()
    assert isinstance(registry, ChunkerRegistry)


def test_build_default_chunker_registry_registers_slide_chunker_for_pptx() -> None:
    registry = build_default_chunker_registry()
    sharepoint_pptx = registry.dispatch(kind="sharepoint", mime=PPTX_MIME, section_kind="text")
    google_pptx = registry.dispatch(kind="google_drive", mime=PPTX_MIME, section_kind="text")
    assert isinstance(sharepoint_pptx, SlideChunker)
    assert isinstance(google_pptx, SlideChunker)


def test_build_default_chunker_registry_registers_sheet_row_chunker_for_xlsx() -> None:
    registry = build_default_chunker_registry()
    sharepoint_xlsx = registry.dispatch(kind="sharepoint", mime=XLSX_MIME, section_kind="tabular")
    sharepoint_legacy = registry.dispatch(kind="sharepoint", mime=LEGACY_XLS_MIME, section_kind="tabular")
    google_xlsx = registry.dispatch(kind="google_drive", mime=XLSX_MIME, section_kind="tabular")
    assert isinstance(sharepoint_xlsx, SheetRowChunker)
    assert isinstance(sharepoint_legacy, SheetRowChunker)
    assert isinstance(google_xlsx, SheetRowChunker)


def test_build_default_chunker_registry_registers_docx_heading_for_docx() -> None:
    registry = build_default_chunker_registry()
    sharepoint_docx = registry.dispatch(kind="sharepoint", mime=DOCX_MIME, section_kind="text")
    google_docx = registry.dispatch(kind="google_drive", mime=DOCX_MIME, section_kind="text")
    assert isinstance(sharepoint_docx, DocxHeadingChunker)
    assert isinstance(google_docx, DocxHeadingChunker)


def test_build_default_chunker_registry_unknown_kind_falls_through_to_fallback() -> None:
    registry = build_default_chunker_registry()
    unknown = registry.dispatch(kind="unknown_source", mime=PPTX_MIME, section_kind="text")
    assert unknown is registry.fallback


def test_fallback_paragraph_split_matches_legacy_silver_shape() -> None:
    """Output paragraphs respect blank-line boundaries — matches Silver chunking."""
    fallback = ParagraphFallbackChunker(version="1")
    text = "para one\n\npara two\n\npara three"
    chunks = fallback.chunk(text=text, section_kind="text", source_uri="x")
    texts = [c.text for c in chunks]
    # All three paragraphs short enough to coalesce into one chunk under the
    # 1000-char budget.
    assert len(texts) == 1
    assert "para one" in texts[0]
    assert "para three" in texts[0]
