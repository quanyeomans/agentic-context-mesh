"""Contract test for the ``gotenberg`` conversion-tier extractor (F43).

Imports the canonical fake AND the real implementation, then runs the
same :class:`Extractor` Protocol assertions against both through one
parametrized body. The fake proves the test seam is real; the real impl
proves the production class satisfies the same shape.

The real :class:`GotenbergExtractor` is constructed with an
:class:`httpx.Client` wired to :class:`httpx.MockTransport` (returns
``%PDF`` bytes) plus a fake ``pdf_extractor`` so neither the gotenberg
service nor the real ``pdf_fallback`` library is driven during the
contract test. The HTTP failure paths (timeout / 4xx / empty body) are
exercised by the unit tests under ``tests/extractors/test_gotenberg.py``.

Sabotage-proofs:

  * Deleting ``version`` from :mod:`kairix.extractors.gotenberg` breaks
    ``test_extractor_declares_version``.
  * Flipping ``can_extract`` to ``return True`` for ``application/pdf``
    on the real impl breaks ``test_real_refuses_pdf_mime``.
  * Flipping the quality gate's char threshold to ``0`` breaks
    ``test_quality_ok_false_on_short_output``.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from kairix.extractors import ExtractedDocument, Extractor
from kairix.extractors.gotenberg import (
    GotenbergExtractor,
)
from kairix.extractors.gotenberg import (
    make_extractor as make_real_extractor,
)
from kairix.extractors.gotenberg import (
    version as gotenberg_version,
)
from tests.fakes import FakeGotenbergExtractor, FakePdfFallbackExtractor

pytestmark = pytest.mark.contract

# Canonical "office format with no in-process extractor" the tier claims.
# Modern OOXML docx is NOW handled in-process (markitdown / docx) and is
# REFUSED by both fake and real — exercised by ``test_can_extract_refuses_ooxml_docx_mime``.
_LEGACY_OFFICE_MIME = "application/msword"
_OOXML_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_PDF_BYTES = b"%PDF-1.7\n" + (b"converted-pdf-payload " * 8)


def _pdf_returning_client() -> httpx.Client:
    """An :class:`httpx.Client` whose convert route returns ``%PDF`` bytes."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_PDF_BYTES)

    return httpx.Client(transport=httpx.MockTransport(_handler))


def _make_real_with_stubs() -> Extractor:
    """Construct the real :class:`GotenbergExtractor` with mocked HTTP + PDF tier."""
    return GotenbergExtractor(
        version=gotenberg_version,
        http_client=_pdf_returning_client(),
        pdf_extractor=FakePdfFallbackExtractor(),
    )


_Factory = Callable[[], Extractor]


@pytest.fixture(
    params=[
        pytest.param(lambda: FakeGotenbergExtractor(), id="fake"),
        pytest.param(_make_real_with_stubs, id="real"),
    ]
)
def _extractor(request: pytest.FixtureRequest) -> Extractor:
    factory: _Factory = request.param
    return factory()


@pytest.mark.contract
def test_gotenberg_extractor_satisfies_protocol() -> None:
    # F43-single-impl: asserts the real class registers as the Extractor
    # Protocol + concrete type — a fake is an Extractor by construction,
    # so co-asserting it would prove nothing about the real wire.
    """The real factory returns an instance that is a runtime ``Extractor``."""
    real = _make_real_with_stubs()
    assert isinstance(real, Extractor)
    assert isinstance(real, GotenbergExtractor)


@pytest.mark.contract
def test_extractor_declares_version() -> None:
    # F43-single-impl: probes the module-level F40 ``version`` literal,
    # not an instance method — there is no fake-side analogue of a
    # module constant to co-assert.
    """F40 requirement — module-level ``version`` is non-empty."""
    assert isinstance(gotenberg_version, str)
    assert gotenberg_version.strip() != ""


@pytest.mark.contract
def test_real_factory_returns_gotenberg_instance() -> None:
    # F43-single-impl: asserts the real entry-point factory returns the
    # real concrete class — the fake has no make_extractor factory, so
    # this is inherently a real-only probe.
    """``make_extractor`` returns a real :class:`GotenbergExtractor`."""
    real = make_real_extractor()
    assert isinstance(real, GotenbergExtractor)
    assert real.name == "gotenberg"


@pytest.mark.contract
def test_can_extract_claims_legacy_office_mime(_extractor: Extractor) -> None:
    """Both fake and real claim a legacy-Office mime (no in-process extractor)."""
    assert _extractor.can_extract(_LEGACY_OFFICE_MIME, b"\xd0\xcf\x11\xe0") is True


@pytest.mark.contract
def test_can_extract_refuses_octet_stream(_extractor: Extractor) -> None:
    """Both fake and real refuse ``application/octet-stream`` (no shadowing)."""
    assert _extractor.can_extract("application/octet-stream", b"PK\x03\x04") is False


@pytest.mark.contract
def test_can_extract_refuses_ooxml_docx_mime(_extractor: Extractor) -> None:
    """Both fake and real REFUSE modern OOXML docx — it's the in-process tiers' job.

    Sabotage proof: re-adding the OOXML mimes to the real
    ``_GOTENBERG_MIMES`` (or the fake's ``_claimed_mimes``) makes
    ``can_extract`` return True, shadowing the in-process markitdown / docx
    extractor and dead-lettering the document when gotenberg's HTTP service
    is absent (see the composed PPTX E2E test).
    """
    assert _extractor.can_extract(_OOXML_DOCX_MIME, b"PK\x03\x04") is False


@pytest.mark.contract
def test_real_refuses_pdf_mime() -> None:
    # F43-single-impl: the parametrized _extractor body already co-asserts
    # the shared refusal contract (octet-stream); this real-only probe
    # pins the production-critical "never shadow pdf_fallback" guarantee
    # on the real wire specifically.
    """The real impl refuses ``application/pdf`` — that's pdf_fallback's job."""
    real = _make_real_with_stubs()
    assert real.can_extract("application/pdf", b"%PDF-1.7") is False


@pytest.mark.contract
def test_extract_returns_document_with_non_empty_markdown(_extractor: Extractor) -> None:
    """``extract`` produces an :class:`ExtractedDocument` with markdown text."""
    doc = _extractor.extract(b"PK\x03\x04" + b"x" * 256, _LEGACY_OFFICE_MIME)
    assert isinstance(doc, ExtractedDocument)
    assert doc.markdown.strip() != ""


@pytest.mark.contract
def test_quality_ok_true_on_substantive_output(_extractor: Extractor) -> None:
    """Quality gate passes when the converted document carries text."""
    doc = _extractor.extract(b"PK\x03\x04" + b"x" * 256, _LEGACY_OFFICE_MIME)
    assert _extractor.quality_ok(doc) is True


@pytest.mark.contract
def test_quality_ok_false_on_short_output(_extractor: Extractor) -> None:
    """Quality gate fails when the converted markdown is below the floor."""
    from kairix.extractors import DocMetadata, Page

    short = ExtractedDocument(
        markdown="x",
        pages=(Page(page_number=1, text="x", has_images=False),),
        images=(),
        metadata=DocMetadata(title=None, author=None, created_date=None, language=None, page_count=1),
        confidence=0.0,
    )
    assert _extractor.quality_ok(short) is False
