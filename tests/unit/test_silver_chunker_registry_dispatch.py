"""PR#6 unit: Silver routes passthrough markdown through the chunker registry.

F5: exercised through the public ``DefaultSilverProcessor(chunker_registry=...)``
surface; a fake registry/chunker is injected via the public constructor param,
never by patching internals.
"""

from __future__ import annotations

import pytest

from kairix.core.connectors.silver import (
    SILVER_MARKDOWN_CHUNKER_VERSION,
    SILVER_PAGE_CHUNKER_VERSION,
    DefaultSilverProcessor,
)
from kairix.core.protocols import BronzeRef, Chunk, DocMetadata, ExtractedDocument, Page

pytestmark = pytest.mark.unit

_PER_TYPE_VERSION = "fake-structural@9.9.9"


class _FakeChunker:
    """Returns one Chunk with a distinctive version + structural metadata."""

    def chunk(self, *, text: str, section_kind: str, source_uri: str) -> tuple[Chunk, ...]:
        return (
            Chunk(
                text=text,
                content_hash="deadbeef",
                source_name="",  # minimal — Silver re-wraps with source metadata
                source_uri=source_uri,
                source_modified_at="",
                source_page=None,
                sensitivity="internal",
                chunker_version=_PER_TYPE_VERSION,
                author=None,
                author_email=None,
                tags=(),
                metadata={"heading_path": "Root > Section", "section_kind": section_kind},
            ),
        )


class _FakeRegistry:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def dispatch(self, *, kind: str, mime: str, section_kind: str) -> _FakeChunker:
        self.calls.append((kind, mime, section_kind))
        return _FakeChunker()


def _bronze() -> BronzeRef:
    return BronzeRef(
        source_name="obsidian",
        item_id="n",
        raw_path=None,
        mime="text/markdown",
        fetched_at="2026-06-25T00:00:00Z",
    )


def _markdown_doc() -> ExtractedDocument:
    return ExtractedDocument(
        markdown="# Title\n\nbody.",
        pages=(),
        images=(),
        metadata=DocMetadata(title="t", author=None, created_date=None, language=None, page_count=None),
        confidence=1.0,
    )


def test_registry_dispatch_stamps_per_type_version_and_keeps_silver_metadata() -> None:
    registry = _FakeRegistry()
    out = DefaultSilverProcessor(chunker_registry=registry).process(
        _bronze(), _markdown_doc(), "src://obsidian/n", "2026-06-25T00:00:00Z", "internal"
    )
    assert registry.calls == [("obsidian", "text/markdown", "text")]  # dispatched by (kind, mime)
    assert out.chunks
    chunk = out.chunks[0]
    assert chunk.chunker_version == _PER_TYPE_VERSION  # per-type version, not the silver fallback
    assert "heading_path" in chunk.metadata  # structural metadata preserved
    assert chunk.source_name == "obsidian"  # Silver re-wraps with source metadata
    assert chunk.sensitivity == "internal"


def test_no_registry_uses_paragraph_fallback() -> None:
    out = DefaultSilverProcessor().process(
        _bronze(), _markdown_doc(), "src://obsidian/n", "2026-06-25T00:00:00Z", "internal"
    )
    assert out.chunks
    assert all(c.chunker_version == SILVER_MARKDOWN_CHUNKER_VERSION for c in out.chunks)


def test_paged_path_unaffected_by_registry() -> None:
    """Page-bearing extracts keep the page chunker even when a registry is wired
    (PR#6 is markdown-first; per-page dispatch lands separately)."""
    registry = _FakeRegistry()
    pages = (Page(page_number=1, text="alpha page body.", has_images=False),)
    doc = ExtractedDocument(
        markdown="alpha page body.",
        pages=pages,
        images=(),
        metadata=DocMetadata(title="t", author=None, created_date=None, language=None, page_count=1),
        confidence=1.0,
    )
    out = DefaultSilverProcessor(chunker_registry=registry).process(
        _bronze(), doc, "src://x", "2026-06-25T00:00:00Z", "internal"
    )
    assert registry.calls == []  # registry NOT consulted for the page path
    assert all(c.chunker_version == SILVER_PAGE_CHUNKER_VERSION for c in out.chunks)
