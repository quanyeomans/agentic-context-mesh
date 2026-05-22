"""Contract test for the ``pdf_fallback`` extractor plugin (F43).

Imports the canonical fake AND the real implementation, then runs the
same :class:`Extractor` Protocol assertions against both. The fake
proves the test seam is real; the real impl proves the production
class satisfies the same shape — without requiring the upstream
``pdfplumber`` library to be present in the contract-test environment.

The real :class:`PdfFallbackExtractor` is constructed with a scripted
``pdf_opener`` so the upstream library is not imported during the
contract test. The library-level import is exercised by the unit
tests under ``tests/extractors/test_pdf_fallback.py`` against the
recorded ``tests/fixtures/extractors/sample.pdf`` fixture.

Sabotage-proofs:

  * Deleting ``version`` from :mod:`kairix.extractors.pdf_fallback`
    breaks ``test_extractor_declares_version``.
  * Flipping ``can_extract`` to ``return True`` for ``text/plain``
    on the real impl breaks ``test_real_rejects_plain_text``.
  * Flipping the quality gate's char threshold to ``0`` breaks
    ``test_quality_ok_false_on_image_only_output``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest

from kairix.extractors import ExtractedDocument, Extractor
from kairix.extractors.pdf_fallback import (
    PdfFallbackExtractor,
)
from kairix.extractors.pdf_fallback import (
    make_extractor as make_real_extractor,
)
from kairix.extractors.pdf_fallback import (
    version as pdf_fallback_version,
)
from tests.fakes import FakePdfFallbackExtractor

pytestmark = pytest.mark.contract


@dataclass
class _StubPage:
    """Stub of the upstream ``pdfplumber.Page`` shape."""

    text: str = ""
    tables: list[list[list[str | None]]] = field(default_factory=list)

    def extract_text(self) -> str | None:
        return self.text or None

    def extract_tables(self) -> list[list[list[str | None]]]:
        return list(self.tables)


@dataclass
class _StubPdf:
    """Stub of the upstream ``pdfplumber.PDF`` shape — context-managed."""

    pages: list[_StubPage] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __enter__(self) -> _StubPdf:
        return self

    def __exit__(self, *args: Any) -> None:
        return None


def _make_real_with_stub(*, page_text: str | None = None) -> Extractor:
    """Construct the real :class:`PdfFallbackExtractor` with a stub opener."""
    body = page_text if page_text is not None else ("Recovered body text from PDF page.\n" * 6)
    pdf = _StubPdf(pages=[_StubPage(text=body)], metadata={"Title": "stub"})

    def _opener(_path: str) -> _StubPdf:
        return pdf

    return PdfFallbackExtractor(
        version=pdf_fallback_version,
        pdf_opener=lambda: _opener,
    )


_Factory = Callable[[], Extractor]


@pytest.fixture(
    params=[
        pytest.param(lambda: FakePdfFallbackExtractor(), id="fake"),
        pytest.param(_make_real_with_stub, id="real"),
    ]
)
def _extractor(request: pytest.FixtureRequest) -> Extractor:
    factory: _Factory = request.param
    return factory()


@pytest.mark.contract
def test_pdf_fallback_extractor_satisfies_protocol() -> None:
    """The real factory returns an instance that is a runtime ``Extractor``."""
    real = _make_real_with_stub()
    assert isinstance(real, Extractor)
    assert isinstance(real, PdfFallbackExtractor)


@pytest.mark.contract
def test_extractor_declares_version() -> None:
    """F40 requirement — module-level ``version`` is non-empty."""
    assert isinstance(pdf_fallback_version, str)
    assert pdf_fallback_version.strip() != ""


@pytest.mark.contract
def test_real_factory_returns_pdf_fallback_instance() -> None:
    """``make_extractor`` returns a real :class:`PdfFallbackExtractor`."""
    real = make_real_extractor()
    assert isinstance(real, PdfFallbackExtractor)
    assert real.name == "pdf_fallback"


@pytest.mark.contract
def test_can_extract_claims_pdf(_extractor: Extractor) -> None:
    """Both fake and real claim ``application/pdf``."""
    assert _extractor.can_extract("application/pdf", b"%PDF-1.4") is True


@pytest.mark.contract
def test_can_extract_claims_pdf_by_magic_bytes(_extractor: Extractor) -> None:
    """Magic-byte sniff catches a PDF served as ``application/octet-stream``."""
    assert _extractor.can_extract("application/octet-stream", b"%PDF-1.7") is True


@pytest.mark.contract
def test_real_rejects_plain_text() -> None:
    """The real impl refuses ``text/plain`` — that's passthrough's job."""
    real = _make_real_with_stub()
    assert real.can_extract("text/plain", b"hello") is False


@pytest.mark.contract
def test_extract_returns_document_with_non_empty_markdown(_extractor: Extractor) -> None:
    """``extract`` produces an :class:`ExtractedDocument` with markdown text."""
    doc = _extractor.extract(b"%PDF-1.4\n" + b"x" * 64, "application/pdf")
    assert isinstance(doc, ExtractedDocument)
    assert doc.markdown.strip() != ""


@pytest.mark.contract
def test_extract_returns_document_with_at_least_one_page(_extractor: Extractor) -> None:
    """``extract`` populates ``pages`` so chunks can cite back per page."""
    doc = _extractor.extract(b"%PDF-1.4\n" + b"x" * 64, "application/pdf")
    assert len(doc.pages) >= 1


@pytest.mark.contract
def test_quality_ok_true_on_substantive_output(_extractor: Extractor) -> None:
    """Quality gate passes when markdown has enough content and a page carries text."""
    doc = _extractor.extract(b"%PDF-1.4\n" + b"x" * 32, "application/pdf")
    assert _extractor.quality_ok(doc) is True


@pytest.mark.contract
def test_quality_ok_false_on_image_only_output() -> None:
    """Quality gate fails when pdfplumber returns empty page text (scanned PDF)."""
    extractor = _make_real_with_stub(page_text="")
    doc = extractor.extract(b"%PDF-1.4\n" + b"y" * 4096, "application/pdf")
    assert extractor.quality_ok(doc) is False
