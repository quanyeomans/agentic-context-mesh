"""Unit tests for :mod:`kairix.extractors.pdf_fallback` (MM-1, Wave 3).

Two seams are exercised:

  1. The **scripted-opener** seam — a fake ``pdf_opener`` returning a
     stub PDF with scripted pages. Used for shape / branch tests that
     don't need the upstream library.
  2. The **real-library** seam — invokes the actual
     :func:`pdfplumber.open` against the recorded PDF fixture under
     ``tests/fixtures/extractors/sample.pdf`` (added by IM-4). Skipped
     when the optional ``pdf_fallback`` extra is not installed.

Sabotage-proof per test:

  * ``test_extract_invokes_opener_factory`` — flipping :meth:`extract`
    to bypass the opener returns no pages and breaks the assertion
    that the scripted page text surfaces in markdown.
  * ``test_quality_ok_false_for_empty_page_text`` — relaxing
    :meth:`quality_ok` to ``return True`` breaks the assertion (the
    scenario the orchestrator routes to OCR).
  * ``test_can_extract_rejects_text_mime`` — broadening
    :meth:`can_extract` (e.g. removing the mime allow-list) breaks
    the assertion.
  * ``test_real_pdf_fixture_round_trips`` — flipping the temp-file
    write to drop the bytes returns near-empty markdown and breaks
    the ``len(...) >= 100`` check.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from kairix.extractors import ExtractedDocument, Page
from kairix.extractors.pdf_fallback import (
    PdfFallbackExtractor,
    make_extractor,
    version,
)

pytestmark = pytest.mark.unit

FIXTURE_PDF = Path(__file__).parent.parent / "fixtures" / "extractors" / "sample.pdf"


@dataclass
class _StubPage:
    """In-memory stand-in for ``pdfplumber.Page``."""

    text: str = ""
    tables: list[list[list[str | None]]] = field(default_factory=list)

    def extract_text(self) -> str | None:
        return self.text or None

    def extract_tables(self) -> list[list[list[str | None]]]:
        return list(self.tables)


@dataclass
class _StubPdf:
    """In-memory stand-in for ``pdfplumber.PDF`` — context-managed."""

    pages: list[_StubPage] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    opens: list[str] = field(default_factory=list)

    def __enter__(self) -> _StubPdf:
        return self

    def __exit__(self, *args: Any) -> None:
        return None


def _make_extractor(
    *,
    pages: list[_StubPage] | None = None,
    metadata: dict[str, Any] | None = None,
) -> tuple[PdfFallbackExtractor, _StubPdf]:
    """Build a real :class:`PdfFallbackExtractor` wired to a stub PDF."""
    stub = _StubPdf(
        pages=pages or [_StubPage(text="recovered text " * 10)],
        metadata=metadata or {"Title": "fixture"},
    )

    def _opener(path: str) -> _StubPdf:
        stub.opens.append(path)
        return stub

    extractor = PdfFallbackExtractor(version=version, pdf_opener=lambda: _opener)
    return extractor, stub


def test_factory_returns_pdf_fallback_instance() -> None:
    extractor = make_extractor()
    assert isinstance(extractor, PdfFallbackExtractor)
    assert extractor.name == "pdf_fallback"
    assert extractor.version == version


def test_version_module_level_non_empty() -> None:
    """F40 sanity — module-level ``version`` is a non-empty string."""
    assert isinstance(version, str)
    assert version.strip() != ""


def test_can_extract_claims_pdf_by_mime() -> None:
    extractor, _ = _make_extractor()
    assert extractor.can_extract("application/pdf", b"") is True


def test_can_extract_claims_pdf_by_magic_bytes() -> None:
    extractor, _ = _make_extractor()
    assert extractor.can_extract("application/octet-stream", b"%PDF-1.7") is True


def test_can_extract_rejects_text_mime() -> None:
    extractor, _ = _make_extractor()
    assert extractor.can_extract("text/plain", b"hello") is False
    assert extractor.can_extract("text/markdown", b"# hi") is False


def test_can_extract_rejects_short_magic_buffer() -> None:
    extractor, _ = _make_extractor()
    assert extractor.can_extract("application/octet-stream", b"%P") is False


def test_extract_invokes_opener_factory() -> None:
    extractor, stub = _make_extractor(
        pages=[_StubPage(text="recovered body line " * 12)],
    )
    raw = b"%PDF-1.4\n" + (b"payload-bytes " * 32)
    doc = extractor.extract(raw, "application/pdf")
    assert isinstance(doc, ExtractedDocument)
    assert "recovered body line" in doc.markdown
    assert len(stub.opens) == 1
    # Confirm the temp-file path (str, ending in .pdf) was passed.
    assert stub.opens[0].endswith(".pdf")


def test_extract_builds_one_page_object_per_pdf_page() -> None:
    extractor, _ = _make_extractor(
        pages=[
            _StubPage(text="page one body text " * 8),
            _StubPage(text="page two body text " * 8),
        ],
    )
    doc = extractor.extract(b"%PDF-1.4\n" + b"x" * 64, "application/pdf")
    assert len(doc.pages) == 2
    assert doc.pages[0].page_number == 1
    assert doc.pages[1].page_number == 2
    assert "page one" in doc.pages[0].text
    assert "page two" in doc.pages[1].text


def test_extract_renders_tables_as_pipe_markdown() -> None:
    extractor, _ = _make_extractor(
        pages=[
            _StubPage(
                text="Body intro.",
                tables=[
                    [
                        ["Name", "Score"],
                        ["Alpha", "10"],
                        ["Beta", "20"],
                    ]
                ],
            )
        ],
    )
    doc = extractor.extract(b"%PDF-1.4\n" + b"x" * 64, "application/pdf")
    assert "| Name | Score |" in doc.markdown
    assert "| --- | --- |" in doc.markdown
    assert "| Alpha | 10 |" in doc.markdown
    assert "| Beta | 20 |" in doc.markdown


def test_extract_propagates_pdf_metadata() -> None:
    extractor, _ = _make_extractor(
        metadata={
            "Title": "PDF Title",
            "Author": "Author Name",
            "CreationDate": "D:20260522000000Z",
            "Subject": "ignored-by-doc-metadata",
        }
    )
    doc = extractor.extract(b"%PDF-1.4\n" + b"x" * 64, "application/pdf")
    assert doc.metadata.title == "PDF Title"
    assert doc.metadata.author == "Author Name"
    assert doc.metadata.created_date == "D:20260522000000Z"
    assert doc.metadata.page_count == 1


def test_extract_handles_missing_metadata() -> None:
    extractor, _ = _make_extractor(metadata={"Producer": "noise"})
    doc = extractor.extract(b"%PDF-1.4\n" + b"x" * 64, "application/pdf")
    assert doc.metadata.title is None
    assert doc.metadata.author is None
    assert doc.metadata.created_date is None
    # Producer is not modelled in DocMetadata; it stays in raw bytes.


def test_extract_handles_non_string_metadata_values() -> None:
    """Non-string metadata values (e.g. byte strings, ints) are dropped to None."""
    extractor, _ = _make_extractor(metadata={"Title": 42, "Author": b"raw"})
    doc = extractor.extract(b"%PDF-1.4\n" + b"x" * 64, "application/pdf")
    assert doc.metadata.title is None
    assert doc.metadata.author is None


def test_extract_returns_zero_confidence_for_empty_bytes() -> None:
    extractor, _ = _make_extractor()
    doc = extractor.extract(b"", "application/pdf")
    assert doc.confidence == 0.0


def test_extract_caps_confidence_at_one() -> None:
    # Page text far longer than the raw bytes — confidence is capped.
    extractor, _ = _make_extractor(pages=[_StubPage(text="x" * 10000)])
    doc = extractor.extract(b"%PDF-1.4\n" + b"x" * 8, "application/pdf")
    assert doc.confidence == 1.0


def test_quality_ok_true_for_text_bearing_pdf() -> None:
    extractor, _ = _make_extractor(
        pages=[_StubPage(text="recovered body text content line. " * 6)],
    )
    doc = extractor.extract(b"%PDF-1.4\n" + b"x" * 32, "application/pdf")
    assert extractor.quality_ok(doc) is True


def test_quality_ok_false_for_empty_page_text() -> None:
    """Image-only PDF: pdfplumber returns empty text — escalate to OCR."""
    extractor, _ = _make_extractor(pages=[_StubPage(text="")])
    doc = extractor.extract(b"%PDF-1.4\n" + b"x" * 4096, "application/pdf")
    assert extractor.quality_ok(doc) is False


def test_quality_ok_false_for_short_markdown() -> None:
    """Tiny recovery below the 100-char floor fails the quality gate."""
    extractor, _ = _make_extractor(pages=[_StubPage(text="brief")])
    doc = extractor.extract(b"%PDF-1.4\n" + b"x" * 4096, "application/pdf")
    assert extractor.quality_ok(doc) is False


def test_quality_ok_false_when_pages_have_whitespace_only_text() -> None:
    """Whitespace-only pages don't satisfy the "at least one page has text" gate.

    Sabotage-proof inline: replacing the whitespace with body text
    flips the result to True. Validated by
    ``test_quality_ok_true_for_text_bearing_pdf`` above.
    """
    long_blob_text = "    \n\t  \n   " * 80  # over 100 chars but whitespace only
    extractor, _ = _make_extractor(pages=[_StubPage(text=long_blob_text)])
    doc = extractor.extract(b"%PDF-1.4\n" + b"x" * 32, "application/pdf")
    # Markdown comes from text.strip() being non-empty so the doc may
    # have empty markdown when the only "content" is whitespace; the
    # quality gate must still return False.
    assert extractor.quality_ok(doc) is False


def test_page_value_object_shape() -> None:
    extractor, _ = _make_extractor(pages=[_StubPage(text="content")])
    doc = extractor.extract(b"%PDF-1.4\n" + b"x" * 8, "application/pdf")
    assert isinstance(doc.pages[0], Page)
    assert doc.pages[0].has_images is False


# ---------------------------------------------------------------------------
# Real-library tests — exercise the actual pdfplumber package against a
# recorded PDF fixture. Skipped when the optional extra is not present.
# ---------------------------------------------------------------------------


def _pdfplumber_available() -> bool:
    try:
        import pdfplumber  # noqa: F401 — probe-only import; resolved at runtime
    except ImportError:
        return False
    return True


@pytest.mark.skipif(
    not _pdfplumber_available(),
    reason="pdf_fallback extra not installed; install via 'pip install Kairix-agentic-knowledge-mgt[pdf_fallback]'",
)
def test_real_pdf_fixture_round_trips() -> None:
    raw = FIXTURE_PDF.read_bytes()
    assert raw.startswith(b"%PDF")
    extractor = make_extractor()
    doc = extractor.extract(raw, "application/pdf")
    assert isinstance(doc, ExtractedDocument)
    assert len(doc.markdown) >= 100
    # The fixture content carries a known phrase — assert it survives the round-trip.
    assert "Hello PDF text" in doc.markdown
    assert len(doc.pages) >= 1
    assert any(page.text.strip() for page in doc.pages)
    assert extractor.quality_ok(doc) is True


@pytest.mark.skipif(
    not _pdfplumber_available(),
    reason="pdf_fallback extra not installed; install via 'pip install Kairix-agentic-knowledge-mgt[pdf_fallback]'",
)
def test_real_pdf_fixture_metadata_present() -> None:
    raw = FIXTURE_PDF.read_bytes()
    extractor = make_extractor()
    doc = extractor.extract(raw, "application/pdf")
    # ``page_count`` is always populated from len(pdf.pages).
    assert doc.metadata.page_count is not None
    assert doc.metadata.page_count >= 1


@pytest.mark.skipif(
    not _pdfplumber_available(),
    reason="pdf_fallback extra not installed; install via 'pip install Kairix-agentic-knowledge-mgt[pdf_fallback]'",
)
def test_real_image_only_pdf_fails_quality_gate(tmp_path: Path) -> None:
    """An image-only PDF (no text content stream) fails quality_ok and
    escalates to OCR.

    Synthesise an image-only PDF in-line so the test doesn't depend
    on an additional checked-in fixture. The minimal shape: a PDF
    with a single page carrying only an image XObject, no text
    content stream — pdfplumber's ``extract_text`` returns ``None``
    and the quality gate must return False.
    """
    # Minimal hand-rolled PDF — one page, no text content stream.
    # The page carries an empty content stream so pdfplumber's
    # extract_text returns None / empty for the page.
    pdf_bytes = (
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]"
        b" /Contents 4 0 R >>\nendobj\n"
        b"4 0 obj\n<< /Length 0 >>\nstream\n\nendstream\nendobj\n"
        b"xref\n0 5\n0000000000 65535 f \n0000000010 00000 n \n"
        b"0000000060 00000 n \n0000000110 00000 n \n0000000180 00000 n \n"
        b"trailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n230\n%%EOF\n"
    )
    image_only = tmp_path / "image_only.pdf"
    image_only.write_bytes(pdf_bytes)
    raw = image_only.read_bytes()
    assert raw.startswith(b"%PDF")
    extractor = make_extractor()
    doc = extractor.extract(raw, "application/pdf")
    # No text content stream — pdfplumber returns no text. quality_ok
    # must return False so the orchestrator escalates to OCR.
    assert extractor.quality_ok(doc) is False
