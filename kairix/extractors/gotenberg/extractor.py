"""gotenberg-backed conversion tier — Office/ODF/Visio/Publisher/RTF → PDF (PR-3).

Office binary formats (legacy ``.doc`` / ``.xls`` / ``.ppt``), the
OpenDocument family (``.odt`` / ``.ods`` / ``.odp`` / ``.odg``), Visio
drawings, Microsoft Publisher, and RTF are not natively recoverable by
the ``markitdown`` / ``pdf_fallback`` tiers — they would dead-letter.
This extractor converts them to PDF via the `gotenberg
<https://gotenberg.dev>`_ HTTP service (LibreOffice under the hood),
then routes the converted PDF back through the registered
``pdf_fallback`` extractor so the document inherits its table
extraction, per-page :class:`Page` objects, and metadata.

It is wired as an escalation tier between ``pdf_fallback`` and ``ocr``::

    markitdown   (default)
        ↓ quality_ok = False / can_extract = False
    pdf_fallback (pdfplumber, table-aware)
        ↓ can_extract = False (not a PDF)
    gotenberg    (this plugin — convert-then-re-enter pdf_fallback)
        ↓ extract raised (gotenberg outage / 4xx)
    ocr          (Tesseract for image-only PDFs)

Design choice (spec §"Decline vs raise"): :meth:`can_extract` returns
``False`` for mimes NOT in :data:`_GOTENBERG_MIMES` — crucially PDF /
text / ``application/octet-stream`` — so this tier never shadows the
PDF-native or passthrough tiers. Modern Word OOXML is opt-in via
``include_docx`` for deployments that prefer page anchors over the
in-process heading-aware Word extractor. When gotenberg is
**unreachable**, **times out**, returns an **empty body**, or returns a
**4xx/5xx**, :meth:`extract` RAISES (so the escalation orchestrator
falls through to the next tier and, if every tier fails, the item
dead-letters for retry). A transient gotenberg outage must stay
retryable, never a silent skip.

Spec ref: ``docs/architecture/connector-ingestion-architecture.md`` §2
("extractors tree"), §3 ("Extractor Protocol"), §4 ("Three failures map
to three behaviours") for ``quality_ok`` semantics, and PR-3.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx
from tenacity import (
    RetryError,
    Retrying,
    retry_if_result,
    stop_after_attempt,
    wait_exponential,
)
from tenacity.wait import wait_base

from kairix.core.protocols import SourceMetadata
from kairix.extractors import (
    ExtractedDocument,
    Extractor,
    MimeType,
)

logger = logging.getLogger(__name__)

#: Canonical plugin name surfaced by the extractor registry.
PLUGIN_NAME = "gotenberg"

#: Name of the registered extractor the converted PDF re-enters. Lazy-
#: resolved via the registry so the chain wiring stays declarative.
_PDF_TIER_NAME = "pdf_fallback"

#: IANA mime claimed by the PDF tier the converted bytes route through.
_PDF_MIME = "application/pdf"

#: PDF magic header (``%PDF``) — gotenberg must hand us a real PDF back.
_MAGIC_PDF = b"%PDF"

#: Minimum decoded-markdown length the tier treats as "quality ok".
#: Mirrors ``pdf_fallback``'s 100-char floor (spec §10 escalation gate)
#: so a converted-then-extracted document escalates to ``ocr`` on the
#: same condition a native PDF would.
_QUALITY_MIN_CHARS = 100

#: Default gotenberg base URL — the laptop / single-VM compose service.
_DEFAULT_GOTENBERG_URL = "http://localhost:3000"
#: Default per-convert deadline (seconds). LibreOffice conversion of a
#: large deck can take tens of seconds; the gate is configurable.
_DEFAULT_TIMEOUT_S = 60.0
#: Default size ceiling (MiB). Files above this are rejected BEFORE the
#: HTTP call so a pathological upload never reaches gotenberg.
_DEFAULT_MAX_FILE_SIZE_MB = 256

#: Retry budget for transient gotenberg responses (429 / 5xx). Mirrors
#: the dex_crm client's 4-attempt exponential-backoff convention.
_MAX_RETRIES = 4
#: Exponential-backoff base (seconds) for the retry loop.
_BACKOFF_BASE_S = 1.0

#: gotenberg LibreOffice conversion route, appended to the base URL.
_CONVERT_ROUTE = "/forms/libreoffice/convert"
#: Multipart form field name gotenberg expects the upload under.
_FORM_FIELD = "files"

#: Error-message prefix shared by every raise path (F17 — one literal).
_ERR_PREFIX = "gotenberg: "

#: Shared "what happens next" tail on every convert-failure raise path —
#: a single literal so the three raise sites can't drift (F17 / S1192).
_NEXT_ESCALATES = "next: the item escalates to ocr / dead-letters for retry."

#: Legacy-Office / ODF / Visio / Publisher / RTF mimes this tier claims.
#: PDF / text / ``application/octet-stream`` AND the modern OOXML mimes
#: (.docx / .pptx / .xlsx) are deliberately ABSENT so the tier never
#: shadows pdf_fallback / passthrough or the in-process OOXML extractors.
_MS_WORD = "application/msword"
_MS_EXCEL = "application/vnd.ms-excel"
_MS_POWERPOINT = "application/vnd.ms-powerpoint"
_ODF_PREFIX = "application/vnd.oasis.opendocument."

#: Modern OOXML (.pptx / .xlsx + their macro / template variants) are
#: deliberately ABSENT: the in-process ``pptx`` / ``xlsx`` tiers already
#: provide slide/sheet anchors. Modern Word OOXML is also absent from the
#: default set, but can be claimed when ``GotenbergExtractorConfig.include_docx``
#: is true for deployments that need page anchors on Word documents.
_GOTENBERG_MIMES: frozenset[str] = frozenset(
    {
        # Legacy Microsoft Office binary formats (no in-process extractor).
        _MS_WORD,
        _MS_EXCEL,
        _MS_POWERPOINT,
        # OpenDocument family (text / spreadsheet / presentation / graphics).
        _ODF_PREFIX + "text",
        _ODF_PREFIX + "spreadsheet",
        _ODF_PREFIX + "presentation",
        _ODF_PREFIX + "graphics",
        # Microsoft Visio.
        "application/vnd.ms-visio.drawing",
        "application/vnd.ms-visio.drawing.macroenabled.12",
        "application/vnd.visio",
        # Microsoft Publisher.
        "application/x-mspublisher",
        # Rich Text Format.
        "application/rtf",
        "text/rtf",
    }
)

#: Modern Word OOXML mimes that Gotenberg can optionally convert for
#: page-aware SharePoint indexing. Disabled by default so generic deployments
#: keep the in-process heading-aware ``docx`` extractor path.
_DOCX_MIMES: frozenset[str] = frozenset(
    {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-word.document.macroenabled.12",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
        "application/vnd.ms-word.template.macroenabled.12",
    }
)

#: Per-mime upload filename extension — gotenberg sniffs the format from
#: the filename it receives, so we hand it the right suffix.
_MIME_TO_EXTENSION: dict[str, str] = {
    _MS_WORD: ".doc",
    _MS_EXCEL: ".xls",
    _MS_POWERPOINT: ".ppt",
    _ODF_PREFIX + "text": ".odt",
    _ODF_PREFIX + "spreadsheet": ".ods",
    _ODF_PREFIX + "presentation": ".odp",
    _ODF_PREFIX + "graphics": ".odg",
    "application/vnd.ms-visio.drawing": ".vsdx",
    "application/vnd.ms-visio.drawing.macroenabled.12": ".vsdm",
    "application/vnd.visio": ".vsd",
    "application/x-mspublisher": ".pub",
    "application/rtf": ".rtf",
    "text/rtf": ".rtf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-word.document.macroenabled.12": ".docm",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.template": ".dotx",
    "application/vnd.ms-word.template.macroenabled.12": ".dotm",
}

#: Fallback upload filename when the mime carries no known extension —
#: gotenberg still attempts LibreOffice format detection on the bytes.
_DEFAULT_UPLOAD_NAME = "document.bin"


@dataclass(frozen=True)
class GotenbergExtractorConfig:
    """Construction-time config for :class:`GotenbergExtractor`.

    Frozen-dataclass per F42. ``gotenberg_url`` points at the conversion
    service — defaults to the laptop / single-VM compose service; the
    deployment compose runs it as ``http://gotenberg:3000``.
    ``timeout_s`` is the per-convert deadline; ``max_file_size_mb`` is
    the pre-HTTP size ceiling. ``include_docx`` opts into Word OOXML
    conversion for page-aware indexing when the connector can depend on
    Gotenberg. Operators override any field via the connector config
    (``extractor_chain_configs.gotenberg``); env defaults flow through
    :func:`kairix.paths.gotenberg_extractor_config` at factory time
    (F4 boundary).
    """

    gotenberg_url: str = _DEFAULT_GOTENBERG_URL
    timeout_s: float = _DEFAULT_TIMEOUT_S
    max_file_size_mb: int = _DEFAULT_MAX_FILE_SIZE_MB
    include_docx: bool = False


def _upload_name(mime: MimeType) -> str:
    """Return the upload filename gotenberg should sniff the format from."""
    suffix = _MIME_TO_EXTENSION.get(mime) if isinstance(mime, str) else None
    return f"document{suffix}" if suffix else _DEFAULT_UPLOAD_NAME


def _is_retryable(response: httpx.Response) -> bool:
    """True for 429 / 5xx — transient gotenberg conditions worth retrying."""
    code = response.status_code
    return code == httpx.codes.TOO_MANY_REQUESTS or code >= httpx.codes.INTERNAL_SERVER_ERROR


class GotenbergExtractor:
    """:class:`Extractor` impl that converts Office/ODF/Visio/RTF to PDF.

    The instance carries the :data:`version` declared in the package
    ``__init__`` so the value flows from one canonical declaration site
    (F40) through to ``documents_media.extractor_version`` on every
    produced document.

    DI seams (all kwargs with real defaults — F6 / F1 clean):

      * ``config`` — :class:`GotenbergExtractorConfig`; defaults to the
        laptop compose endpoint.
      * ``http_client`` — :class:`httpx.Client`; defaults to a fresh
        client built at first request. Tests pass a stand-in wired to
        ``httpx.MockTransport`` so the suite never reaches the network.
      * ``pdf_extractor`` — the :class:`Extractor` the converted PDF
        re-enters. Defaults to ``None``; the real ``pdf_fallback`` tier
        is lazy-resolved via the registry on first :meth:`extract`.
        Tests inject a fake to assert the convert→re-enter chain.
      * ``retry_wait`` — the tenacity wait strategy for the transient
        (429 / 5xx) retry loop. Defaults to ``None`` → production
        exponential backoff. Unit tests pass ``wait_none()`` so the
        retry-budget assertion costs zero wall-clock.
    """

    def __init__(
        self,
        *,
        version: str,
        config: GotenbergExtractorConfig | None = None,
        http_client: httpx.Client | None = None,
        pdf_extractor: Extractor | None = None,
        retry_wait: wait_base | None = None,
    ) -> None:
        """Construct the extractor with explicit ``version`` + injectable seams."""
        self.name: str = PLUGIN_NAME
        self.version: str = version
        self._config = config or GotenbergExtractorConfig()
        self._http_client = http_client
        self._pdf_extractor = pdf_extractor
        self._retry_wait: wait_base = retry_wait or wait_exponential(multiplier=_BACKOFF_BASE_S, exp_base=2)

    def can_extract(self, mime: MimeType, _magic_bytes: bytes) -> bool:
        """``True`` only for legacy-Office/ODF/Visio/Publisher/RTF mimes.

        Claims solely by mime — these formats share container magic
        bytes (ZIP for ODF, OLE2 for legacy Office) that can't be
        disambiguated from the leading bytes alone, so a magic-byte
        sniff would mis-claim. Crucially REFUSES ``application/pdf``,
        ``text/*``, ``application/octet-stream``, AND the modern OOXML
        mimes (.docx / .pptx / .xlsx + macro / template variants) — the
        latter are handled in-process by the ``markitdown`` / ``pptx`` /
        ``docx`` / ``xlsx`` tiers, so claiming them here would shadow a
        working extractor and dead-letter when gotenberg is absent.

        ``_magic_bytes`` is ``_``-prefixed (F19) — the mime allow-list
        is the only signal consulted.
        """
        if not isinstance(mime, str):
            return False
        if mime in _GOTENBERG_MIMES:
            return True
        return self._config.include_docx and mime in _DOCX_MIMES

    def extract(self, raw: bytes, mime: MimeType) -> ExtractedDocument:
        """Convert ``raw`` to PDF via gotenberg, then re-enter ``pdf_fallback``.

        Sequence: size-gate (raise :class:`ValueError` BEFORE any HTTP
        call) → POST the bytes to gotenberg's LibreOffice route → assert
        a non-empty ``%PDF`` body → hand the converted PDF to the
        ``pdf_fallback`` tier's ``extract(pdf_bytes, "application/pdf")``
        and return its :class:`ExtractedDocument`.

        Raises (so escalation falls through to ``ocr`` / the item
        dead-letters for retry):

          * :class:`ValueError` — file exceeds ``max_file_size_mb``, or
            gotenberg returned an empty / non-PDF body.
          * :class:`RuntimeError` — gotenberg unreachable, timed out, or
            returned a 4xx/5xx after the retry budget.
        """
        self._reject_oversize(raw)
        pdf_bytes = self._convert_to_pdf(raw, mime)
        return self._pdf_tier().extract(pdf_bytes, _PDF_MIME)

    def quality_ok(self, doc: ExtractedDocument) -> bool:
        """Escalation gate mirroring ``pdf_fallback`` (spec §10).

        Returns ``True`` only when the extracted markdown clears the
        :data:`_QUALITY_MIN_CHARS` floor AND at least one page carries
        non-empty text. A converted PDF that yields no text layer (e.g.
        a Visio drawing that rasterised to images) fails the gate and
        escalates to ``ocr``. A ``False`` here is a soft escalation
        signal, not a hard error.
        """
        if len(doc.markdown) < _QUALITY_MIN_CHARS:
            return False
        return any(page.text.strip() for page in doc.pages)

    def metadata_for(self, _raw: bytes, _mime: MimeType) -> SourceMetadata:
        """Return empty :class:`SourceMetadata`.

        ADR-021 (Wave E.5): body-level metadata for converted documents
        is surfaced by the ``pdf_fallback`` tier the bytes re-enter (PDF
        Info-dict extraction lands there). Stub keeps the Protocol
        surface satisfied.
        """
        return SourceMetadata()

    def _reject_oversize(self, raw: bytes) -> None:
        """Raise :class:`ValueError` when ``raw`` exceeds the size ceiling.

        Enforced BEFORE the HTTP call so a pathological upload never
        reaches gotenberg (spec §"Config / env").
        """
        ceiling = self._config.max_file_size_mb * 1024 * 1024
        if len(raw) > ceiling:
            raise ValueError(
                f"{_ERR_PREFIX}file is {len(raw)} bytes, over the "
                f"{self._config.max_file_size_mb} MB convert ceiling. "
                f"fix: raise extractor_chain_configs.gotenberg.max_file_size_mb "
                f"(or KAIRIX_GOTENBERG_MAX_FILE_SIZE_MB) for this connector. "
                f"next: oversize binaries dead-letter; review the source item."
            )

    def _convert_to_pdf(self, raw: bytes, mime: MimeType) -> bytes:
        """POST ``raw`` to gotenberg and return the converted PDF bytes.

        Retries 429 / 5xx with exponential backoff (4 attempts), then
        raises on exhaustion. Connect / read timeouts and unreachable
        hosts raise :class:`RuntimeError` so escalation continues.
        """
        response = self._send_with_retry(raw, mime)
        pdf_bytes = response.content
        if not pdf_bytes.startswith(_MAGIC_PDF):
            raise ValueError(
                f"{_ERR_PREFIX}convert returned a non-PDF / empty body "
                f"({len(pdf_bytes)} bytes). "
                f"fix: confirm the gotenberg service is healthy and the source "
                f"format is one LibreOffice can open. "
                f"{_NEXT_ESCALATES}"
            )
        return pdf_bytes

    def _send_with_retry(self, raw: bytes, mime: MimeType) -> httpx.Response:
        """POST the multipart upload, retrying transient gotenberg responses.

        Mirrors the dex_crm client retry shape: ``tenacity`` with
        exponential backoff on 429 / 5xx, ``reraise`` so a genuinely
        unreachable / timing-out gotenberg surfaces a typed
        :class:`RuntimeError` to the escalation orchestrator. Never logs
        the gotenberg URL beyond debug (F15).
        """
        client = self._ensure_client()
        url = self._config.gotenberg_url.rstrip("/") + _CONVERT_ROUTE
        files = {_FORM_FIELD: (_upload_name(mime), raw)}
        logger.debug("gotenberg: posting %d bytes to convert route", len(raw))

        retrying = Retrying(
            retry=retry_if_result(_is_retryable),
            wait=self._retry_wait,
            stop=stop_after_attempt(_MAX_RETRIES),
            reraise=True,
        )
        try:
            response = retrying(client.post, url, files=files)
        except RetryError as exc:
            final: httpx.Response = exc.last_attempt.result()
            self._raise_status(final)
            return final  # pragma: no cover — _raise_status always raises for 4xx/5xx
        except httpx.HTTPError as exc:
            raise RuntimeError(
                f"{_ERR_PREFIX}convert request failed ({type(exc).__name__}). "
                f"fix: confirm the gotenberg service is reachable and not "
                f"overloaded; check the compose 'gotenberg' service health. "
                f"{_NEXT_ESCALATES}"
            ) from exc
        self._raise_status(response)
        return response

    def _raise_status(self, response: httpx.Response) -> None:
        """Raise :class:`RuntimeError` on a 4xx/5xx convert response.

        A 4xx means LibreOffice could not open the source format; a 5xx
        means gotenberg itself failed. Both escalate (raise) rather than
        silently skip so the failure stays retryable.
        """
        if response.is_success:
            return
        raise RuntimeError(
            f"{_ERR_PREFIX}convert returned HTTP {response.status_code}. "
            f"fix: a 4xx means LibreOffice could not open this format; a 5xx "
            f"means gotenberg failed — check the 'gotenberg' service logs. "
            f"{_NEXT_ESCALATES}"
        )

    def _ensure_client(self) -> httpx.Client:
        """Lazy-build the underlying ``httpx.Client`` on first use."""
        if self._http_client is None:
            self._http_client = httpx.Client(timeout=self._config.timeout_s)
        return self._http_client

    def _pdf_tier(self) -> Extractor:
        """Return the ``pdf_fallback`` extractor the converted PDF re-enters.

        Lazy-resolves the registered ``pdf_fallback`` factory on first
        use (F1 seam: tests inject ``pdf_extractor=`` directly). Caches
        the resolved instance so the registry lookup happens once.
        """
        if self._pdf_extractor is None:
            from kairix.core.connectors.registry import resolve_extractor

            self._pdf_extractor = resolve_extractor(_PDF_TIER_NAME)()
        return self._pdf_extractor
