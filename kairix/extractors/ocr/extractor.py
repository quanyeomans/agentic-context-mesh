"""OCR-backed extractor for scanned PDFs and image-only pages (MM-2).

The third member of the escalation chain — invoked when both
``markitdown`` and ``pdf_fallback`` report ``quality_ok() = False``.
The pipeline is responsibility of the orchestrator, not the
extractor — this class happily accepts a PDF that pdfplumber
rejected (per the brief: "OCR happily accepts a PDF that pdfplumber
rejected").

The :meth:`extract` method runs the KFEAT-012 Addendum chain:

    1. Render each PDF page to an image (via :mod:`pdfplumber` —
       already a transitive dep of the ``markitdown`` extra and the
       MM-1 ``pdf_fallback`` plugin).
    2. Run the pre-processing chain
       (:func:`kairix.extractors.ocr.preprocess.run_chain`):
       DPI normalise → greyscale → deskew → denoise → binarise.
    3. Detect orientation via Tesseract ``--psm 0`` and rotate to
       portrait if the rotation is non-zero.
    4. Run Tesseract on the cleaned image and collect per-word
       confidence via :func:`pytesseract.image_to_data`.
    5. Reconstruct reading order (single-column passthrough today;
       multi-column detector lands in Wave 3).
    6. Aggregate into an :class:`ExtractedDocument`; the per-doc
       ``confidence`` is the mean per-page mean-word-confidence
       normalised to ``[0, 1]`` (Tesseract reports ``[0, 100]``).

Spec ref: ``docs/architecture/connector-ingestion-architecture.md``
§10 (Wave 3 MM-2) + ``KFEAT-012 Addendum``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

import numpy as np

from kairix.core.protocols import SourceMetadata
from kairix.extractors import (
    DocMetadata,
    ExtractedDocument,
    MimeType,
    Page,
)
from kairix.extractors.ocr.layout import (
    reconstruct_reading_order,
    whole_page_region,
)
from kairix.extractors.ocr.preprocess import (
    TARGET_DPI,
    run_chain,
)
from kairix.extractors.ocr.tesseract_runner import (
    OcrRunner,
    TesseractRunner,
)

#: Type alias for the pre-processing chain — accepts a uint8 ndarray
#: (the page image as rendered) and returns a uint8 ndarray (cleaned
#: image ready for Tesseract). Defaults to
#: :func:`kairix.extractors.ocr.preprocess.run_chain` in production;
#: tests pass a passthrough lambda when ``opencv-python-headless``
#: isn't available.
Preprocessor = Callable[[np.ndarray], np.ndarray]

#: Canonical plugin name surfaced by the extractor registry.
PLUGIN_NAME = "ocr"

#: Minimum decoded-text length the plugin treats as "quality ok".
#: Anything shorter is treated as a recognition failure and escalates
#: to the vision LLM (Phase 3, out of scope for MM-2).
_QUALITY_MIN_CHARS = 50

#: Minimum mean per-word confidence (in ``[0, 1]``) the plugin treats
#: as "quality ok". Below 0.6 Tesseract reports low certainty across
#: the page — the document either has heavy artefacts or a font /
#: script the engine struggles with; escalate to vision.
_QUALITY_MIN_CONFIDENCE = 0.6

#: Image mime types the OCR plugin claims natively.
_IMAGE_MIMES = frozenset({"image/png", "image/jpeg", "image/jpg", "image/tiff", "image/bmp"})

#: PDF mime + magic header.
_PDF_MIME = "application/pdf"
_MAGIC_PDF = b"%PDF"


class PageRenderer(Protocol):
    """Wire-shape Protocol for the PDF-page-to-image renderer.

    The production renderer wraps :mod:`pdfplumber`; tests pass a
    scripted fake that returns pre-built ``numpy.ndarray`` page
    images (e.g. a synthetic greyscale square with simulated text).
    Both implementations honour the ``raw -> tuple[ndarray, ...]``
    shape so the extractor's call site is invariant.
    """

    def render(self, raw: bytes, mime: MimeType) -> tuple[np.ndarray, ...]:
        """Render ``raw`` bytes into one ``numpy.ndarray`` per page."""


def _default_page_renderer() -> PageRenderer:
    """Lazy-import the pdfplumber-backed renderer.

    The lazy import keeps ``import kairix.extractors.ocr`` cheap and
    free of upstream-deps when the ``ocr`` extra isn't installed.
    """
    return _PdfPlumberRenderer()


class _PdfPlumberRenderer:
    """Production :class:`PageRenderer` — uses :mod:`pdfplumber`.

    Why pdfplumber rather than ``pdf2image``: pdfplumber is already a
    transitive dependency of the ``markitdown`` extra (and the MM-1
    ``pdf_fallback`` plugin), so the ``ocr`` extra avoids dragging in
    another image-toolkit + the system-side ``poppler`` binary
    ``pdf2image`` requires. ``Page.to_image()`` ships in every
    pdfplumber version we support.
    """

    def render(self, raw: bytes, mime: MimeType) -> tuple[np.ndarray, ...]:
        if mime in _IMAGE_MIMES:
            return (_decode_image_bytes(raw),)
        return _render_pdf_pages(raw)


def _decode_image_bytes(raw: bytes) -> np.ndarray:
    """Decode raw image bytes (PNG/JPEG/TIFF/BMP) to a uint8 array."""
    try:
        import io

        from PIL import Image
    except ImportError as exc:  # pragma: no cover — import path validated by test
        raise RuntimeError(
            "ocr: the 'Pillow' package is not installed. "
            "fix: pip install 'Kairix-agentic-knowledge-mgt[ocr]' "
            "to opt into the OCR extractor. "
            "next: re-run the connector sync; the OCR plugin will then resolve."
        ) from exc
    with Image.open(io.BytesIO(raw)) as img:
        img.load()
        return np.array(img)


def _render_pdf_pages(raw: bytes) -> tuple[np.ndarray, ...]:
    """Render every page of a PDF byte stream to a uint8 ndarray."""
    try:
        import io

        import pdfplumber
    except ImportError as exc:  # pragma: no cover — import path validated by test
        raise RuntimeError(
            "ocr: the 'pdfplumber' package is not installed. "
            "fix: pip install 'Kairix-agentic-knowledge-mgt[ocr]' "
            "to opt into the OCR extractor. "
            "next: re-run the connector sync; the OCR plugin will then resolve."
        ) from exc
    pages: list[np.ndarray] = []
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        for page in pdf.pages:
            pil = page.to_image(resolution=TARGET_DPI).original
            pages.append(np.array(pil))
    return tuple(pages)


class OcrExtractor:
    """:class:`Extractor` impl that delegates to Tesseract via :mod:`pytesseract`.

    The instance carries the :data:`version` declared in the package
    ``__init__`` (per F40) so the value flows from one canonical
    declaration site through to ``documents_media.extractor_version``
    on every produced document.

    Test seam: the constructor accepts ``page_renderer=`` and
    ``ocr_runner=`` so a contract / unit test passes scripted fakes
    without monkeypatching :mod:`pdfplumber`, :mod:`pytesseract`, or
    :mod:`PIL` (F1-clean). The default factories defer to the
    production lazy imports; environments without the ``ocr`` extra
    raise a typed ``RuntimeError`` only when ``extract`` is actually
    called, not at module import time.
    """

    def __init__(
        self,
        *,
        version: str,
        page_renderer: Callable[[], PageRenderer] = _default_page_renderer,
        ocr_runner: Callable[[], OcrRunner] = TesseractRunner,
        preprocessor: Preprocessor = run_chain,
    ) -> None:
        """Construct the extractor with explicit ``version`` + factories.

        ``preprocessor`` defaults to the production pre-processing
        chain (DPI → greyscale → deskew → denoise → binarise). Tests
        that don't have :mod:`opencv-python-headless` available inject
        an identity function instead.
        """
        self.name: str = PLUGIN_NAME
        self.version: str = version
        self._page_renderer_factory = page_renderer
        self._ocr_runner_factory = ocr_runner
        self._preprocessor = preprocessor

    def can_extract(self, mime: MimeType, magic_bytes: bytes) -> bool:
        """``True`` for ``application/pdf`` and the common image mimes.

        Per the brief: the OCR plugin claims its mime types regardless
        of whether earlier members of the escalation chain might have
        also claimed them. The orchestrator's escalation chain decides
        which extractor actually runs against a given byte stream.
        """
        if isinstance(mime, str) and mime in _IMAGE_MIMES:
            return True
        if isinstance(mime, str) and mime == _PDF_MIME:
            return True
        return magic_bytes.startswith(_MAGIC_PDF)

    def extract(self, raw: bytes, mime: MimeType) -> ExtractedDocument:
        """Run the OCR pipeline against ``raw``.

        Page rendering + OCR are delegated to the injected factories
        so a test can drive deterministic behaviour without invoking
        the upstream :mod:`pdfplumber` or :mod:`pytesseract` packages.
        """
        renderer = self._page_renderer_factory()
        runner = self._ocr_runner_factory()
        page_images = renderer.render(raw, mime)
        pages = tuple(
            _ocr_one_page(img, index=i, runner=runner, preprocessor=self._preprocessor)
            for i, img in enumerate(page_images, start=1)
        )
        markdown = _pages_to_markdown(pages)
        confidence = _aggregate_confidence(pages)
        return ExtractedDocument(
            markdown=markdown,
            pages=pages,
            images=(),
            metadata=DocMetadata(
                title=None,
                author=None,
                created_date=None,
                language=None,
                page_count=len(pages) or None,
            ),
            confidence=confidence,
        )

    def quality_ok(self, doc: ExtractedDocument) -> bool:
        """Escalation gate per KFEAT-012 Addendum.

        Returns ``True`` only when:

          * the extracted markdown has at least
            :data:`_QUALITY_MIN_CHARS` characters of content, AND
          * the per-doc confidence is at least
            :data:`_QUALITY_MIN_CONFIDENCE` (the normalised Tesseract
            mean-word-confidence).

        Below 0.6 confidence Tesseract reports low certainty across
        the document; the orchestrator escalates to the vision LLM
        (Phase 3 — out of scope for MM-2).
        """
        text = doc.markdown.strip()
        if len(text) < _QUALITY_MIN_CHARS:
            return False
        return doc.confidence >= _QUALITY_MIN_CONFIDENCE

    def metadata_for(self, _raw: bytes, _mime: MimeType) -> SourceMetadata:
        """Return empty :class:`SourceMetadata`.

        ADR-021 (Wave E.5): OCR does not surface document-body
        metadata — the source is rasterised pixels with no envelope.
        F65 envelope authority lives on the connector for
        OCR-targeted sources.
        """
        return SourceMetadata()


# ---------------------------------------------------------------------------
# Helpers — kept module-private (underscore prefix) so they can be
# refactored without touching the public surface. Each is small and
# single-responsibility per F16 (cognitive complexity ≤ 15).
# ---------------------------------------------------------------------------


def _ocr_one_page(
    img: np.ndarray,
    *,
    index: int,
    runner: OcrRunner,
    preprocessor: Preprocessor,
) -> Page:
    """Run the pre-process + Tesseract chain on a single page image."""
    cleaned = preprocessor(img)
    cleaned = _apply_orientation(cleaned, runner=runner)
    recognition = runner.recognise_text(cleaned)
    text = _regions_to_text(cleaned, recognition.text)
    confidence = _normalise_confidence(recognition.mean_confidence)
    return Page(
        page_number=index,
        text=_attach_page_confidence(text, confidence),
        has_images=False,
    )


def _apply_orientation(img: np.ndarray, *, runner: OcrRunner) -> np.ndarray:
    """Rotate ``img`` to portrait when Tesseract reports a non-zero angle.

    Tesseract's orientation detector returns ``rotation_degrees`` in
    ``{0, 90, 180, 270}``. We only act on non-zero values; the
    rotation uses :func:`numpy.rot90` to avoid pulling another OpenCV
    call into this hot path (numpy is already imported).
    """
    orientation = runner.detect_orientation(img)
    if orientation.rotation_degrees == 0:
        return img
    # numpy.rot90 rotates counter-clockwise by 90deg per ``k`` step.
    # Tesseract reports the clockwise angle the page is rotated by
    # relative to upright, so we apply the inverse.
    k = (-orientation.rotation_degrees // 90) % 4
    return np.rot90(img, k=k)


def _regions_to_text(img: np.ndarray, recognised_text: str) -> str:
    """Wrap the recognised text into the layout-region pipeline.

    MM-2 ships the single-column path; the multi-column detector is
    deferred to Wave 3. The seam exists so the extractor's call site
    stays invariant when the column detector lands.
    """
    h, w = img.shape[:2]
    region = whole_page_region(recognised_text, width=int(w), height=int(h))
    return reconstruct_reading_order((region,))


def _attach_page_confidence(text: str, confidence: float) -> str:
    """Embed the per-page confidence value in the page text.

    The OCR extractor surfaces per-page confidence so downstream
    chunking can weight chunks by certainty; until SC-1 Page carries
    a ``confidence`` field directly we encode it as a leading
    HTML-style comment that the silver layer strips before
    chunking.
    """
    return f"<!-- ocr-confidence: {confidence:.3f} -->\n{text}".rstrip()


def _normalise_confidence(tesseract_conf: float) -> float:
    """Normalise Tesseract's ``[0, 100]`` to ``[0, 1]``, clamped."""
    if tesseract_conf <= 0:
        return 0.0
    if tesseract_conf >= 100:
        return 1.0
    return tesseract_conf / 100.0


def _aggregate_confidence(pages: tuple[Page, ...]) -> float:
    """Per-doc confidence = mean of per-page confidences.

    Per-page confidence is recovered from the leading comment marker
    so the aggregator stays decoupled from the :class:`Page` dataclass
    until the SC-1 schema lands a first-class confidence field.
    """
    if not pages:
        return 0.0
    confs = [_extract_page_confidence(page.text) for page in pages]
    return float(sum(confs) / len(confs))


def _extract_page_confidence(text: str) -> float:
    """Recover the per-page confidence from the embedded comment."""
    marker = "<!-- ocr-confidence: "
    end = " -->"
    if not text.startswith(marker):
        return 0.0
    head = text.split("\n", 1)[0]
    payload = head.removeprefix(marker).removesuffix(end).strip()
    try:
        return float(payload)
    except ValueError:
        return 0.0


def _pages_to_markdown(pages: tuple[Page, ...]) -> str:
    """Join per-page text into the document-level markdown blob.

    Per-page confidence comments are stripped from the unified
    markdown rendering — they are observability data, not content.
    A simple ``## Page <n>`` heading per page keeps chunking able to
    cite back to a specific page later.
    """
    blocks: list[str] = []
    for page in pages:
        body = _strip_page_confidence(page.text)
        if not body.strip():
            continue
        blocks.append(f"## Page {page.page_number}\n\n{body.strip()}")
    return "\n\n".join(blocks)


def _strip_page_confidence(text: str) -> str:
    """Remove the leading confidence-marker line, if present."""
    if not text.startswith("<!-- ocr-confidence:"):
        return text
    parts = text.split("\n", 1)
    return parts[1] if len(parts) == 2 else ""
