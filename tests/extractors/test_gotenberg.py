"""Unit tests for :mod:`kairix.extractors.gotenberg` (PR-3 conversion tier).

The extractor converts Office/ODF/Visio/Publisher/RTF to PDF via the
gotenberg HTTP service, then re-enters the registered ``pdf_fallback``
tier. Two seams are exercised, both F1-clean (no monkeypatching):

  1. The **HTTP seam** — an :class:`httpx.Client` wired to
     :class:`httpx.MockTransport` so the convert request never reaches
     the network. The transport returns ``%PDF`` bytes (success),
     raises :class:`httpx.TimeoutException` (timeout), or returns an
     empty / 4xx / 5xx body (failure) per scenario.
  2. The **pdf-tier seam** — a fake :class:`Extractor` injected via
     ``pdf_extractor=`` so the test asserts the converted PDF chains
     through to the PDF tier without resolving the real ``pdf_fallback``.

Sabotage-proof per test:

  * ``test_extract_converts_then_chains_to_pdf_tier`` — dropping the
    convert→re-enter call returns no markdown and breaks the assertion
    that the fake PDF tier received the ``%PDF`` bytes.
  * ``test_can_extract_refuses_pdf_text_octet`` — broadening
    :meth:`can_extract` (e.g. claiming PDF) breaks the assertion.
  * ``test_extract_oversize_raises_with_no_http_call`` — moving the
    size gate after the HTTP call records a request and breaks the
    "no HTTP call" assertion.
  * ``test_extract_timeout_raises`` / ``..._empty_body_raises`` /
    ``..._http_4xx_raises`` — softening any raise path to "return an
    empty doc" breaks the ``pytest.raises`` assertion (and would let a
    transient gotenberg outage silently skip the item).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx
import pytest
from tenacity import wait_none

from kairix.extractors import DocMetadata, ExtractedDocument, MimeType, Page
from kairix.extractors.gotenberg import (
    GotenbergExtractor,
    GotenbergExtractorConfig,
    make_extractor,
    version,
)

#: The production retry budget (``stop_after_attempt(4)`` in the
#: extractor). Pinned here as a test-local literal rather than imported
#: from the module's private ``_MAX_RETRIES`` — F5 forbids importing
#: ``_``-prefixed internals into tests. The 5xx retry test asserts the
#: tenacity loop makes exactly this many attempts before raising.
_EXPECTED_MAX_RETRIES = 4

pytestmark = pytest.mark.unit

# The convert-chain / failure-path tests drive a mime gotenberg actually
# claims. The modern OOXML docx mime is NO LONGER claimed (it is handled
# in-process by markitdown / docx), so we use a legacy-Office mime
# (.doc) as the canonical "format with no in-process extractor" case.
_DOC_MIME = "application/msword"
#: Modern OOXML docx mime — gotenberg deliberately REFUSES this (the
#: in-process markitdown / docx tiers own it). Asserted refused below.
_OOXML_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_PDF_MIME = "application/pdf"
_PDF_BYTES = b"%PDF-1.7\n" + (b"converted-pdf-payload " * 8)


@dataclass
class _FakePdfTier:
    """In-memory stand-in for the ``pdf_fallback`` :class:`Extractor`.

    Records every ``(raw, mime)`` it is handed so a test can assert the
    convert→re-enter chain wired correctly, and returns a scripted
    :class:`ExtractedDocument`.
    """

    name: str = "pdf_fallback"
    version: str = "0.0.0-fake"
    document: ExtractedDocument | None = None
    calls: list[tuple[bytes, MimeType]] = field(default_factory=list)

    def can_extract(self, mime: MimeType, magic_bytes: bytes) -> bool:
        return mime == _PDF_MIME or magic_bytes.startswith(b"%PDF")

    def extract(self, raw: bytes, mime: MimeType) -> ExtractedDocument:
        self.calls.append((raw, mime))
        return self.document or _doc("recovered office body text line. " * 6)

    def quality_ok(self, doc: ExtractedDocument) -> bool:  # pragma: no cover — not exercised here
        return len(doc.markdown) >= 100

    def metadata_for(self, raw: bytes, mime: MimeType):  # pragma: no cover — not exercised here
        from kairix.core.protocols import SourceMetadata

        return SourceMetadata()


def _doc(markdown: str, *, pages: tuple[Page, ...] | None = None) -> ExtractedDocument:
    """Build a minimal :class:`ExtractedDocument` for the fake PDF tier."""
    return ExtractedDocument(
        markdown=markdown,
        pages=pages if pages is not None else (Page(page_number=1, text=markdown, has_images=False),),
        images=(),
        metadata=DocMetadata(title=None, author=None, created_date=None, language=None, page_count=1),
        confidence=0.5,
    )


def _mock_client(handler) -> httpx.Client:
    """Wrap a request handler in an :class:`httpx.Client` via MockTransport."""
    return httpx.Client(transport=httpx.MockTransport(handler))


def _make_extractor(
    *,
    handler=None,
    pdf_tier: _FakePdfTier | None = None,
    config: GotenbergExtractorConfig | None = None,
) -> tuple[GotenbergExtractor, _FakePdfTier]:
    """Build a real :class:`GotenbergExtractor` wired to test seams.

    ``retry_wait=wait_none()`` removes the production exponential backoff
    so the transient-retry paths assert their budget at zero wall-clock —
    a unit test must never sleep on real tenacity backoff.
    """
    tier = pdf_tier or _FakePdfTier()
    client = _mock_client(handler) if handler is not None else None
    extractor = GotenbergExtractor(
        version=version,
        config=config,
        http_client=client,
        pdf_extractor=tier,
        retry_wait=wait_none(),
    )
    return extractor, tier


# ---------------------------------------------------------------------------
# Factory + version
# ---------------------------------------------------------------------------


def test_factory_returns_gotenberg_instance() -> None:
    extractor = make_extractor()
    assert isinstance(extractor, GotenbergExtractor)
    assert extractor.name == "gotenberg"
    assert extractor.version == version


def test_factory_accepts_config_override() -> None:
    cfg = GotenbergExtractorConfig(gotenberg_url="http://gotenberg:3000", timeout_s=5.0, max_file_size_mb=8)
    extractor = make_extractor(config=cfg)
    assert isinstance(extractor, GotenbergExtractor)


def test_factory_coerces_raw_dict_config() -> None:
    """Registry passes per-member YAML as a raw dict — the factory must coerce it.

    ``build_extractor_from_entry`` resolves
    ``extractor_chain_configs.gotenberg`` and calls
    ``make_extractor(**{"config": {...}})`` — so ``config`` arrives as a
    plain ``dict``, NOT a :class:`GotenbergExtractorConfig`. Without
    coercion the extractor stores the dict and the first ``extract``
    raises ``AttributeError`` on ``self._config.max_file_size_mb``. This
    pins the coercion so every documented operator deployment works.
    """
    extractor = make_extractor(
        config={"gotenberg_url": "http://gotenberg:3000", "timeout_s": 5.0, "max_file_size_mb": 8}
    )
    assert isinstance(extractor, GotenbergExtractor)
    # The raw dict was coerced into a real frozen config dataclass.
    assert isinstance(extractor._config, GotenbergExtractorConfig)
    assert extractor._config.gotenberg_url == "http://gotenberg:3000"
    assert extractor._config.timeout_s == 5.0
    assert extractor._config.max_file_size_mb == 8

    # The oversize gate reads ``max_file_size_mb`` off the coerced config
    # without AttributeError — an 8 MB ceiling rejects a 9 MB upload.
    oversize = b"PK\x03\x04" + b"x" * (9 * 1024 * 1024)
    with pytest.raises(ValueError, match="convert ceiling"):
        extractor.extract(oversize, _DOC_MIME)


def test_version_module_level_non_empty() -> None:
    """F40 sanity — module-level ``version`` is a non-empty string."""
    assert isinstance(version, str)
    assert version.strip() != ""


def test_config_is_frozen() -> None:
    """F42 sanity — the config dataclass is frozen."""
    cfg = GotenbergExtractorConfig()
    with pytest.raises((AttributeError, TypeError)):
        cfg.gotenberg_url = "http://evil"  # type: ignore[misc] — assigning to a frozen-dataclass field on purpose to prove F42 immutability; the assignment must raise


# ---------------------------------------------------------------------------
# can_extract — claims legacy-Office/ODF/Visio/Publisher/RTF, refuses
# pdf/text/octet-stream AND the modern OOXML (docx/pptx/xlsx) mimes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mime",
    [
        # Legacy Microsoft Office binary formats — no in-process extractor.
        "application/msword",
        "application/vnd.ms-excel",
        "application/vnd.ms-powerpoint",
        # OpenDocument family.
        "application/vnd.oasis.opendocument.text",
        "application/vnd.oasis.opendocument.spreadsheet",
        "application/vnd.oasis.opendocument.presentation",
        "application/vnd.oasis.opendocument.graphics",
        # Visio / Publisher / RTF.
        "application/vnd.ms-visio.drawing",
        "application/vnd.visio",
        "application/x-mspublisher",
        "application/rtf",
        "text/rtf",
    ],
)
def test_can_extract_claims_legacy_office_odf_visio_mimes(mime: str) -> None:
    extractor, _ = _make_extractor()
    assert extractor.can_extract(mime, b"PK\x03\x04") is True


@pytest.mark.parametrize(
    "mime",
    [
        # Modern OOXML — owned by the in-process markitdown / pptx / docx /
        # xlsx tiers. gotenberg must REFUSE these so it never shadows the
        # working in-process extractor (and dead-letter when the gotenberg
        # HTTP service is absent).
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document.macroenabled.12",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.macroenabled.12",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.template",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation.macroenabled.12",
        "application/vnd.openxmlformats-officedocument.presentationml.template",
    ],
)
def test_can_extract_refuses_ooxml_mimes(mime: str) -> None:
    """Modern OOXML (docx/pptx/xlsx) is handled in-process — gotenberg refuses it.

    Sabotage proof: re-adding any OOXML mime to ``_GOTENBERG_MIMES`` makes
    gotenberg claim it, shadowing the in-process extractor and (in the
    composed path) dead-lettering the document when the gotenberg HTTP
    service is absent. See the composed PPTX E2E test
    (``tests/e2e/test_composed_connector_sharepoint_path.py``).
    """
    extractor, _ = _make_extractor()
    assert extractor.can_extract(mime, b"PK\x03\x04") is False


@pytest.mark.parametrize(
    "mime",
    [
        "application/pdf",
        "text/plain",
        "text/markdown",
        "text/html",
        "application/octet-stream",
    ],
)
def test_can_extract_refuses_pdf_text_octet(mime: str) -> None:
    """The tier must never shadow pdf_fallback / passthrough."""
    extractor, _ = _make_extractor()
    # Even with PDF magic bytes present, the mime allow-list refuses these.
    assert extractor.can_extract(mime, b"%PDF-1.7") is False


# ---------------------------------------------------------------------------
# extract — convert then chain to pdf_fallback
# ---------------------------------------------------------------------------


def test_extract_converts_then_chains_to_pdf_tier() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=_PDF_BYTES)

    extractor, tier = _make_extractor(handler=handler)
    raw = b"PK\x03\x04" + (b"docx-zip-payload " * 16)
    doc = extractor.extract(raw, _DOC_MIME)

    # Chained to the PDF tier with the converted %PDF bytes + pdf mime.
    assert isinstance(doc, ExtractedDocument)
    assert len(tier.calls) == 1
    chained_raw, chained_mime = tier.calls[0]
    assert chained_raw == _PDF_BYTES
    assert chained_mime == _PDF_MIME
    assert "recovered office body text" in doc.markdown

    # The convert request hit the LibreOffice route as multipart.
    assert len(requests) == 1
    assert requests[0].url.path == "/forms/libreoffice/convert"
    assert requests[0].method == "POST"


def test_extract_posts_to_configured_url() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, content=_PDF_BYTES)

    cfg = GotenbergExtractorConfig(gotenberg_url="http://gotenberg:3000/")
    extractor, _ = _make_extractor(handler=handler, config=cfg)
    extractor.extract(b"PK\x03\x04" + b"x" * 64, _DOC_MIME)
    # Trailing slash on the base URL is normalised, route appended once.
    assert seen == ["http://gotenberg:3000/forms/libreoffice/convert"]


# ---------------------------------------------------------------------------
# Failure paths — oversize / timeout / empty / 4xx / 5xx all RAISE
# ---------------------------------------------------------------------------


def test_extract_oversize_raises_with_no_http_call() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover — must NOT be reached
        calls.append(request)
        return httpx.Response(200, content=_PDF_BYTES)

    cfg = GotenbergExtractorConfig(max_file_size_mb=1)
    extractor, tier = _make_extractor(handler=handler, config=cfg)
    oversize = b"PK\x03\x04" + b"x" * (2 * 1024 * 1024)  # 2 MB > 1 MB ceiling
    with pytest.raises(ValueError, match="convert ceiling"):
        extractor.extract(oversize, _DOC_MIME)
    # The size gate fires BEFORE any HTTP call and before the PDF tier.
    assert calls == []
    assert tier.calls == []


def test_extract_timeout_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("gotenberg convert timed out", request=request)

    extractor, tier = _make_extractor(handler=handler)
    with pytest.raises(RuntimeError, match="gotenberg:"):
        extractor.extract(b"PK\x03\x04" + b"x" * 64, _DOC_MIME)
    assert tier.calls == []


def test_extract_unreachable_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    extractor, _ = _make_extractor(handler=handler)
    with pytest.raises(RuntimeError, match="gotenberg:"):
        extractor.extract(b"PK\x03\x04" + b"x" * 64, _DOC_MIME)


def test_extract_empty_body_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"")

    extractor, tier = _make_extractor(handler=handler)
    with pytest.raises(ValueError, match="non-PDF / empty body"):
        extractor.extract(b"PK\x03\x04" + b"x" * 64, _DOC_MIME)
    assert tier.calls == []


def test_extract_non_pdf_body_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not a pdf</html>")

    extractor, _ = _make_extractor(handler=handler)
    with pytest.raises(ValueError, match="non-PDF / empty body"):
        extractor.extract(b"PK\x03\x04" + b"x" * 64, _DOC_MIME)


def test_extract_http_4xx_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # LibreOffice could not open the format → gotenberg 400.
        return httpx.Response(400, content=b"bad format")

    extractor, tier = _make_extractor(handler=handler)
    with pytest.raises(RuntimeError, match="HTTP 400"):
        extractor.extract(b"PK\x03\x04" + b"x" * 64, _DOC_MIME)
    assert tier.calls == []


def test_extract_http_5xx_raises_after_retry() -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(503, content=b"gotenberg overloaded")

    extractor, _ = _make_extractor(handler=handler)
    with pytest.raises(RuntimeError, match="HTTP 503"):
        extractor.extract(b"PK\x03\x04" + b"x" * 64, _DOC_MIME)
    # 5xx is retryable — the tenacity loop exhausts the full retry budget
    # (stop-after-4) before raising. wait_none() keeps it fast.
    assert len(attempts) == _EXPECTED_MAX_RETRIES


# ---------------------------------------------------------------------------
# quality_ok — mirrors the pdf_fallback gate (>=100 chars + a text-bearing page)
# ---------------------------------------------------------------------------


def test_quality_ok_true_for_text_bearing_doc() -> None:
    extractor, _ = _make_extractor()
    doc = _doc("recovered converted body content line. " * 6)
    assert extractor.quality_ok(doc) is True


def test_quality_ok_false_for_short_markdown() -> None:
    extractor, _ = _make_extractor()
    doc = _doc("brief")
    assert extractor.quality_ok(doc) is False


def test_quality_ok_false_for_empty_page_text() -> None:
    """A converted-but-image-only PDF (no text layer) escalates to ocr."""
    extractor, _ = _make_extractor()
    long_blob = "padding padding padding padding padding padding " * 4  # > 100 chars
    doc = _doc(long_blob, pages=(Page(page_number=1, text="", has_images=True),))
    assert extractor.quality_ok(doc) is False


# ---------------------------------------------------------------------------
# metadata_for — empty SourceMetadata like the sibling extractors
# ---------------------------------------------------------------------------


def test_metadata_for_returns_empty_source_metadata() -> None:
    from kairix.core.protocols import SourceMetadata

    extractor, _ = _make_extractor()
    meta = extractor.metadata_for(b"PK\x03\x04", _DOC_MIME)
    assert isinstance(meta, SourceMetadata)
    assert meta.author is None
    assert meta.tags == ()
