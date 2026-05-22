"""Step definitions for ``extractor_pdf_fallback.feature`` (MM-1, Wave 3).

Drives the real :class:`kairix.extractors.pdf_fallback.PdfFallbackExtractor`
with a fake ``pdf_opener=`` that returns a scripted in-memory PDF —
F1-clean (no monkeypatch), F2-clean (no env mutation). The fake
opener is the canonical seam for behaviour tests; the real upstream
``pdfplumber`` library is exercised by the unit tests under
``tests/extractors/test_pdf_fallback.py`` against the recorded
``tests/fixtures/extractors/sample.pdf`` fixture.

Step phrasings carry the literal phrase "pdf_fallback" so the global
pytest-bdd step registry doesn't collide with the markitdown /
passthrough features' analogous Given/When/Then phrases.

Sabotage-proof per step:
  * "claims the mime type" — flipping ``can_extract`` to return
    ``False`` in production fails the step.
  * "carries non-empty markdown" — flipping ``extract`` to return
    empty markdown in production fails the step.
  * "carries at least one page with non-empty text" — clearing every
    page's text layer in production fails the step.
  * "extractor's version string is non-empty" — clearing the
    module-level ``version`` constant in production fails the step.
  * "quality_ok false for the produced document" (escalation gate) —
    flipping ``quality_ok`` to return ``True`` unconditionally fails
    the @error scenario.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.extractors import ExtractedDocument
from kairix.extractors.pdf_fallback import (
    PdfFallbackExtractor,
    make_extractor,
    version,
)

pytestmark = pytest.mark.bdd


@dataclass
class _FakePdfPage:
    """In-memory stand-in for ``pdfplumber.Page``."""

    text: str = ""
    tables: list[list[list[str | None]]] = field(default_factory=list)

    def extract_text(self) -> str | None:
        return self.text or None

    def extract_tables(self) -> list[list[list[str | None]]]:
        return list(self.tables)


@dataclass
class _FakePdf:
    """In-memory stand-in for ``pdfplumber.PDF`` — context-managed."""

    pages: list[_FakePdfPage] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __enter__(self) -> _FakePdf:
        return self

    def __exit__(self, *args: Any) -> None:
        return None


def _make_opener(pdf: _FakePdf):
    """Return a fake :func:`pdfplumber.open` callable that yields ``pdf``."""

    def _opener(_path: str) -> _FakePdf:
        return pdf

    return _opener


@pytest.fixture
def pdf_fallback_state() -> dict[str, Any]:
    """Per-scenario state container."""
    return {
        "extractor": None,
        "raw": b"",
        "claimed": None,
        "doc": None,
        "pdf": None,
    }


def _build_extractor(state: dict[str, Any], *, pages: list[_FakePdfPage]) -> PdfFallbackExtractor:
    fake_pdf = _FakePdf(pages=pages, metadata={"Title": "Fixture"})
    state["pdf"] = fake_pdf
    return PdfFallbackExtractor(version=version, pdf_opener=lambda: _make_opener(fake_pdf))


@given(parsers.parse('the pdf_fallback extractor is registered under the name "{name}"'))
def _register_pdf_fallback(pdf_fallback_state: dict[str, Any], name: str) -> None:
    real = make_extractor()
    assert isinstance(real, PdfFallbackExtractor)
    assert real.name == name
    body_text = "Recovered body text from PDF page one.\n" * 6
    pdf_fallback_state["extractor"] = _build_extractor(
        pdf_fallback_state,
        pages=[_FakePdfPage(text=body_text)],
    )


@given("the operator has raw bytes for a small PDF with a text content stream")
def _pdf_bytes(pdf_fallback_state: dict[str, Any]) -> None:
    pdf_fallback_state["raw"] = b"%PDF-1.4\n" + (b"text-body " * 32)


@given('the operator hands pdf_fallback raw bytes whose first four bytes are "%PDF"')
def _pdf_magic_bytes(pdf_fallback_state: dict[str, Any]) -> None:
    pdf_fallback_state["raw"] = b"%PDF-1.4\n%magic-only"


@given("the upstream pdfplumber returns empty page text for the supplied bytes")
def _scanned_pdf_bytes(pdf_fallback_state: dict[str, Any]) -> None:
    pdf_fallback_state["raw"] = b"%PDF-1.4\n" + (b"\x00" * 512)
    pdf_fallback_state["extractor"] = _build_extractor(
        pdf_fallback_state,
        pages=[_FakePdfPage(text="")],
    )


@when(parsers.parse('the operator asks the pdf_fallback extractor whether it can extract mime "{mime}"'))
def _ask_can_extract(pdf_fallback_state: dict[str, Any], mime: str) -> None:
    extractor: PdfFallbackExtractor = pdf_fallback_state["extractor"]
    pdf_fallback_state["claimed"] = extractor.can_extract(mime, pdf_fallback_state["raw"][:8])


@when("the operator invokes the pdf_fallback extractor's extract method on the bytes")
def _invoke_extract(pdf_fallback_state: dict[str, Any]) -> None:
    extractor: PdfFallbackExtractor = pdf_fallback_state["extractor"]
    pdf_fallback_state["doc"] = extractor.extract(pdf_fallback_state["raw"], "application/pdf")


@then("the pdf_fallback extractor claims the mime type")
def _then_claims(pdf_fallback_state: dict[str, Any]) -> None:
    assert pdf_fallback_state["claimed"] is True


@then("the pdf_fallback extractor does not claim the mime type")
def _then_does_not_claim(pdf_fallback_state: dict[str, Any]) -> None:
    assert pdf_fallback_state["claimed"] is False


@then("the pdf_fallback document carries non-empty markdown")
def _then_non_empty(pdf_fallback_state: dict[str, Any]) -> None:
    doc: ExtractedDocument = pdf_fallback_state["doc"]
    assert isinstance(doc, ExtractedDocument)
    assert doc.markdown.strip() != ""


@then("the pdf_fallback document carries at least one page with non-empty text")
def _then_has_page_text(pdf_fallback_state: dict[str, Any]) -> None:
    doc: ExtractedDocument = pdf_fallback_state["doc"]
    assert len(doc.pages) >= 1
    assert any(page.text.strip() for page in doc.pages)


@then("the pdf_fallback extractor reports quality_ok true for the produced document")
def _then_quality_ok_true(pdf_fallback_state: dict[str, Any]) -> None:
    extractor: PdfFallbackExtractor = pdf_fallback_state["extractor"]
    doc: ExtractedDocument = pdf_fallback_state["doc"]
    assert extractor.quality_ok(doc) is True


@then("the pdf_fallback extractor reports quality_ok false for the produced document")
def _then_quality_ok_false(pdf_fallback_state: dict[str, Any]) -> None:
    extractor: PdfFallbackExtractor = pdf_fallback_state["extractor"]
    doc: ExtractedDocument = pdf_fallback_state["doc"]
    assert extractor.quality_ok(doc) is False


@then("the pdf_fallback extractor's version string is non-empty")
def _then_version_non_empty(pdf_fallback_state: dict[str, Any]) -> None:
    extractor: PdfFallbackExtractor = pdf_fallback_state["extractor"]
    assert isinstance(extractor.version, str)
    assert extractor.version.strip() != ""
