"""MM-3 integration test — Silver attributes chunks to source pages.

End-to-end proof that ``DefaultSilverProcessor`` honours the per-page
citation contract: when an :class:`ExtractedDocument` carries three
pages, the emitted chunks divide cleanly across the three pages and
each chunk's ``source_page`` matches the page it came from.

Sabotage anchor: mutate ``DefaultSilverProcessor.process`` to always
emit ``source_page=None`` (or to fall through to the markdown-only
branch); rerun this file. The page-distribution assertion fails.
Tested locally 2026-05-22 by forcing the paged branch to
``source_page=None``; the assertion collapsed in <1s.
"""

from __future__ import annotations

import pytest

from kairix.core.connectors.silver import DefaultSilverProcessor
from kairix.core.protocols import (
    BronzeRef,
    DocMetadata,
    ExtractedDocument,
    Page,
)

pytestmark = pytest.mark.integration


def _bronze_ref(source_name: str = "pdf-source") -> BronzeRef:
    return BronzeRef(
        source_name=source_name,
        item_id="report-q1.pdf",
        raw_path=f"{source_name}/report-q1.pdf",
        mime="application/pdf",
        fetched_at="2026-05-22T00:00:00Z",
    )


def _three_page_doc() -> ExtractedDocument:
    pages = (
        Page(
            page_number=1,
            text="Page-one introduction. The Acme Corp quarterly results report.",
            has_images=False,
        ),
        Page(
            page_number=2,
            text="Page-two financials. Revenue grew across every segment.",
            has_images=False,
        ),
        Page(
            page_number=3,
            text="Page-three outlook. Next quarter expansion planning.",
            has_images=False,
        ),
    )
    return ExtractedDocument(
        markdown=(
            "Page-one introduction. The Acme Corp quarterly results report.\n\n"
            "Page-two financials. Revenue grew across every segment.\n\n"
            "Page-three outlook. Next quarter expansion planning."
        ),
        pages=pages,
        images=(),
        metadata=DocMetadata(
            title="Q1 Report",
            author="Acme Corp",
            created_date="2026-04-01",
            language="en",
            page_count=3,
        ),
        confidence=0.95,
    )


def test_silver_attributes_each_chunk_to_its_source_page() -> None:
    """Three-page document → each chunk's ``source_page`` matches its origin page."""
    silver = DefaultSilverProcessor()
    out = silver.process(
        _bronze_ref(),
        _three_page_doc(),
        "src://pdf-source/report-q1.pdf",
        "2026-05-22T00:00:00Z",
        "internal",
    )

    assert len(out.chunks) >= 3, "expected at least one chunk per page"

    # Every chunk carries an integer source_page (never None when pages exist).
    for chunk in out.chunks:
        assert isinstance(chunk.source_page, int), (
            f"chunk on page {chunk.source_page!r} did not receive an integer page number"
        )

    # Per-page chunks must match each page's distinctive marker text.
    page_markers = {1: "Page-one", 2: "Page-two", 3: "Page-three"}
    for chunk in out.chunks:
        assert chunk.source_page is not None
        marker = page_markers[chunk.source_page]
        assert marker in chunk.text, (
            f"chunk attributed to page {chunk.source_page} does not contain "
            f"that page's marker {marker!r} — attribution drifted"
        )

    # All three pages are represented in the output.
    pages_seen = {c.source_page for c in out.chunks}
    assert pages_seen == {1, 2, 3}


def test_silver_paged_chunks_keep_f39_metadata() -> None:
    """MM-3 must not break F39 — paged chunks still carry the three required fields."""
    out = DefaultSilverProcessor().process(
        _bronze_ref(),
        _three_page_doc(),
        "src://pdf-source/report-q1.pdf",
        "2026-05-22T00:00:00Z",
        "internal",
    )
    for chunk in out.chunks:
        assert chunk.source_uri == "src://pdf-source/report-q1.pdf"
        assert chunk.source_modified_at == "2026-05-22T00:00:00Z"
        assert chunk.sensitivity == "internal"
