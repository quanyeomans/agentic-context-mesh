"""Unit tests for :class:`kairix.core.connectors.silver.DefaultSilverProcessor`.

MM-3 — per-page citation. Silver chunking now branches on
``ExtractedDocument.pages``:

* When ``pages`` is empty (passthrough markdown), every chunk has
  ``source_page=None`` — preserves Wave 1 behaviour.
* When ``pages`` is non-empty (PDF / PPTX / XLSX), the chunker emits
  per-page chunks; each chunk's ``source_page`` is the
  ``page.page_number`` it came from.

Sabotage-proof: edit ``DefaultSilverProcessor.process`` so the paged
branch always emits ``source_page=None``, run this file, observe the
``test_silver_paged_extract_attributes_source_page`` assertion fail,
then restore.
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

pytestmark = pytest.mark.unit


def _bronze_ref() -> BronzeRef:
    return BronzeRef(
        source_name="paged-source",
        item_id="doc-1",
        raw_path="paged-source/doc-1",
        mime="application/pdf",
        fetched_at="2026-05-22T00:00:00Z",
    )


def _doc_metadata(page_count: int | None) -> DocMetadata:
    return DocMetadata(
        title="Paged Document",
        author=None,
        created_date=None,
        language=None,
        page_count=page_count,
    )


def test_silver_no_pages_all_chunks_have_none_source_page() -> None:
    """Passthrough markdown (no extractor pages) → every chunk's ``source_page`` is None."""
    doc = ExtractedDocument(
        markdown="Hello.\n\nThis is a passthrough markdown document with no extractor pages.",
        pages=(),
        images=(),
        metadata=_doc_metadata(None),
        confidence=1.0,
    )
    out = DefaultSilverProcessor().process(_bronze_ref(), doc, "src://passthrough/1", "2026-05-22T00:00:00Z", "public")
    assert len(out.chunks) >= 1
    for chunk in out.chunks:
        assert chunk.source_page is None


def test_silver_paged_extract_attributes_source_page() -> None:
    """Two-page document → chunks carry source_page=1 and source_page=2 respectively."""
    pages = (
        Page(page_number=1, text="Page one alpha body content.", has_images=False),
        Page(page_number=2, text="Page two bravo body content.", has_images=False),
    )
    doc = ExtractedDocument(
        markdown="Page one alpha body content.\n\nPage two bravo body content.",
        pages=pages,
        images=(),
        metadata=_doc_metadata(2),
        confidence=1.0,
    )
    out = DefaultSilverProcessor().process(_bronze_ref(), doc, "src://paged/1", "2026-05-22T00:00:00Z", "public")
    pages_seen = [c.source_page for c in out.chunks]
    assert pages_seen, "expected at least one chunk per page"
    # First chunk attributes to page 1, last attributes to page 2.
    assert out.chunks[0].source_page == 1
    assert out.chunks[-1].source_page == 2
    # Every chunk attributes to either page 1 or page 2 — none leak past.
    assert all(p in (1, 2) for p in pages_seen)


def test_silver_paged_chunks_never_straddle_pages() -> None:
    """Per-page chunking → no chunk carries text from two different pages.

    Sabotage anchor: if Silver ever stitches paragraphs across page
    boundaries, the alpha + bravo markers will co-occur in a single
    chunk and this assertion fails.
    """
    pages = (
        Page(page_number=1, text="alpha-marker section one.", has_images=False),
        Page(page_number=2, text="bravo-marker section two.", has_images=False),
        Page(page_number=3, text="charlie-marker section three.", has_images=False),
    )
    doc = ExtractedDocument(
        markdown=("alpha-marker section one.\n\nbravo-marker section two.\n\ncharlie-marker section three."),
        pages=pages,
        images=(),
        metadata=_doc_metadata(3),
        confidence=1.0,
    )
    out = DefaultSilverProcessor().process(_bronze_ref(), doc, "src://paged/2", "2026-05-22T00:00:00Z", "internal")
    for chunk in out.chunks:
        # A chunk attributed to page N must contain only that page's marker.
        markers_in_chunk = sum(marker in chunk.text for marker in ("alpha-marker", "bravo-marker", "charlie-marker"))
        assert markers_in_chunk == 1, (
            f"chunk on page {chunk.source_page} carries {markers_in_chunk} page markers — "
            "chunker straddled a page boundary"
        )


def test_silver_paged_chunks_inherit_f39_metadata() -> None:
    """F39 carries through to every paged chunk — source_uri / source_modified_at / sensitivity."""
    pages = (
        Page(page_number=1, text="Quarterly report page one.", has_images=False),
        Page(page_number=2, text="Quarterly report page two.", has_images=False),
    )
    doc = ExtractedDocument(
        markdown="Quarterly report content.",
        pages=pages,
        images=(),
        metadata=_doc_metadata(2),
        confidence=0.95,
    )
    out = DefaultSilverProcessor().process(
        _bronze_ref(), doc, "src://paged/quarterly", "2026-05-22T00:00:00Z", "internal"
    )
    for chunk in out.chunks:
        assert chunk.source_uri == "src://paged/quarterly"
        assert chunk.source_modified_at == "2026-05-22T00:00:00Z"
        assert chunk.sensitivity == "internal"
        # Paged branch must populate source_page; never None when pages exist.
        assert chunk.source_page is not None


# ---------------------------------------------------------------------------
# Bug B regression — oversized single paragraphs (v2026.5.26a1 dogfood)
# ---------------------------------------------------------------------------


# Public-surface bounds: the chunker target lives behind silver.process(); the
# chunker's promised soft bound (sentence+word boundaries don't land exactly)
# is asserted directly here so the tests stay F5-clean (no _-prefixed imports).
_CHUNKER_TARGET = 1000
_CHUNKER_SOFT_BOUND = 1500


def test_chunker_splits_oversized_single_paragraph_via_silver_process() -> None:
    """Bug B (v2026.5.26a1 SharePoint dogfood): a single paragraph longer
    than the chunk target used to land as ONE chunk regardless of size —
    observed in production as a 32,595-char chunk against a 1000-char
    target. Fix splits at sentence boundaries when a paragraph exceeds
    the target; a single sentence over the target falls back to word,
    then char boundaries.

    Drives DefaultSilverProcessor.process — the public Silver contract
    surface — so the test stays F5-clean.

    Sabotage proof: revert silver._chunk_markdown to the pre-Bug-B
    shape (skip the ``expanded`` expansion, iterate raw ``paragraphs``)
    and the chunk-count assertion below fails with len(chunks)==1.
    """
    sentence = "This is a sentence with enough words to be a meaningful unit of content. "
    paragraph = (sentence * (_CHUNKER_TARGET * 5 // len(sentence) + 1)).strip()  # ~5000 chars

    doc = ExtractedDocument(markdown=paragraph, pages=(), images=(), metadata=_doc_metadata(None), confidence=0.95)
    out = DefaultSilverProcessor().process(
        _bronze_ref(), doc, "src://oversized-para/1", "2026-05-22T00:00:00Z", "public"
    )

    assert len(out.chunks) >= 5, (
        f"Bug B: oversized paragraph (~{len(paragraph)} chars vs target={_CHUNKER_TARGET}) "
        f"must split into >=5 chunks; got {len(out.chunks)}"
    )
    for i, chunk in enumerate(out.chunks):
        assert len(chunk.text) <= _CHUNKER_SOFT_BOUND, (
            f"Bug B: chunk {i} is {len(chunk.text)} chars; expected <= {_CHUNKER_SOFT_BOUND}. "
            f"Chunk head: {chunk.text[:100]!r}"
        )


def test_chunker_splits_oversized_sentence_at_word_boundary_via_silver_process() -> None:
    """Bug B edge case: a single SENTENCE longer than the target (no
    period within target distance — pathological but real) must split
    at word boundaries rather than emit one giant chunk."""
    word = "supercalifragilisticexpialidocious"
    pseudo_sentence = " ".join([word] * (_CHUNKER_TARGET * 3 // (len(word) + 1) + 1))

    doc = ExtractedDocument(
        markdown=pseudo_sentence, pages=(), images=(), metadata=_doc_metadata(None), confidence=0.95
    )
    out = DefaultSilverProcessor().process(
        _bronze_ref(), doc, "src://oversized-sentence/1", "2026-05-22T00:00:00Z", "public"
    )

    assert len(out.chunks) >= 3, (
        f"Bug B: pathological single-sentence paragraph (~{len(pseudo_sentence)} chars) "
        f"must split into >=3 chunks at word boundaries; got {len(out.chunks)}"
    )
    for chunk in out.chunks:
        assert not chunk.text.endswith(word[:5]), f"chunk appears to mid-word-truncate: ends with {chunk.text[-40:]!r}"
        assert len(chunk.text) <= _CHUNKER_SOFT_BOUND


def test_chunker_preserves_short_paragraphs_unchanged_via_silver_process() -> None:
    """Bug B regression-lock — the fix must NOT break the happy path where
    paragraphs fit comfortably within the target. Short paragraphs should
    still be glued greedily into one chunk."""
    md = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
    doc = ExtractedDocument(markdown=md, pages=(), images=(), metadata=_doc_metadata(None), confidence=0.95)
    out = DefaultSilverProcessor().process(_bronze_ref(), doc, "src://short-paras/1", "2026-05-22T00:00:00Z", "public")

    assert len(out.chunks) == 1
    assert "First paragraph." in out.chunks[0].text
    assert "Second paragraph." in out.chunks[0].text
    assert "Third paragraph." in out.chunks[0].text
