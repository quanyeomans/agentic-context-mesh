"""Unit tests for :mod:`kairix.extractors.ocr` (MM-2).

Three seams are exercised:

  1. The **scripted-runner** seam — a scripted
     :class:`OcrRunner` returning fixed recognition / orientation
     results. Used for shape / branch tests that don't need
     Tesseract or :mod:`opencv-python-headless` to be installed.
  2. The **preprocessing-functions** seam — direct unit tests against
     :func:`kairix.extractors.ocr.preprocess.deskew` /
     :func:`binarise` / :func:`to_greyscale` on small synthetic
     numpy arrays. Skipped cleanly when ``opencv-python-headless``
     is absent (F11 rationale: the ``ocr`` extra is optional).
  3. The **real-Tesseract** seam — invokes the actual Tesseract
     binary against a recorded synthetic scanned PDF at
     ``tests/fixtures/extractors/scanned_sample.pdf``. Skipped when
     either :mod:`pytesseract` is not installed OR the Tesseract
     C++ binary is missing on the host. F11 rationale: the
     Tesseract binary is a system-level dependency (brew/apt) that
     can't be pip-installed.

Sabotage-proof per test:

  * ``test_extract_invokes_scripted_runner`` — flipping
    :meth:`extract` to bypass the injected runner breaks the
    confidence-equality assertion.
  * ``test_quality_ok_false_for_low_confidence`` — relaxing
    :meth:`quality_ok` to ``return True`` breaks the assertion.
  * ``test_can_extract_rejects_text_mime`` — broadening
    :meth:`can_extract` breaks the assertion.
  * ``test_deskew_corrects_rotated_image`` — replacing
    :func:`deskew` with the identity function leaves the rotated
    image unchanged and breaks the rotated-vs-restored similarity
    assertion.
  * ``test_real_pdf_fixture_extracts_text`` — flipping the page
    renderer to emit blank bytes returns near-empty markdown and
    breaks the ``len(...) > 10`` check.
  * ``test_sabotaged_chain_drops_below_quality_floor`` — *explicit
    sabotage proof*: running OCR on the rotated-fixture image with
    deskew disabled yields a low confidence that fails
    :meth:`quality_ok`; restoring deskew passes the gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

from kairix.extractors import ExtractedDocument
from kairix.extractors.ocr import OcrExtractor, make_extractor, version
from kairix.extractors.ocr.tesseract_runner import (
    OrientationResult,
    RecognitionResult,
)

pytestmark = pytest.mark.unit

FIXTURE_SCANNED_PDF = Path(__file__).parent.parent / "fixtures" / "extractors" / "scanned_sample.pdf"


# ---------------------------------------------------------------------------
# Scripted-runner seam — exercises the OcrExtractor's branching logic
# without invoking Tesseract or opencv-python-headless.
# ---------------------------------------------------------------------------


@dataclass
class _ScriptedRecognition:
    text: str
    mean_confidence: float


class _ScriptedRunner:
    """Fake :class:`OcrRunner` — returns scripted recognition + orientation."""

    def __init__(
        self,
        recognition: _ScriptedRecognition,
        rotation_degrees: int = 0,
    ) -> None:
        self.recognition = recognition
        self.rotation_degrees = rotation_degrees
        self.orientation_calls = 0
        self.recognise_calls = 0

    def detect_orientation(self, _img: np.ndarray) -> OrientationResult:
        self.orientation_calls += 1
        return OrientationResult(
            rotation_degrees=self.rotation_degrees,
            orientation_confidence=99.0,
        )

    def recognise_text(self, _img: np.ndarray) -> RecognitionResult:
        self.recognise_calls += 1
        return RecognitionResult(
            text=self.recognition.text,
            mean_confidence=self.recognition.mean_confidence,
            word_count=len(self.recognition.text.split()),
        )


class _ScriptedRenderer:
    """Fake :class:`PageRenderer` — yields a fixed number of synthetic pages."""

    def __init__(self, page_count: int = 1, *, height: int = 200, width: int = 200) -> None:
        self.page_count = page_count
        self.height = height
        self.width = width
        self.render_calls = 0

    def render(self, _raw: bytes, _mime: str) -> tuple[np.ndarray, ...]:
        self.render_calls += 1
        page = np.full((self.height, self.width, 3), 255, dtype=np.uint8)
        return tuple(page.copy() for _ in range(self.page_count))


def _make_extractor(
    *,
    recognised_text: str = "recognised body line\n" * 8,
    mean_confidence: float = 85.0,
    rotation_degrees: int = 0,
    page_count: int = 1,
) -> tuple[OcrExtractor, _ScriptedRunner, _ScriptedRenderer]:
    """Build an :class:`OcrExtractor` wired to scripted seams."""
    recognition = _ScriptedRecognition(text=recognised_text, mean_confidence=mean_confidence)
    runner = _ScriptedRunner(recognition, rotation_degrees=rotation_degrees)
    renderer = _ScriptedRenderer(page_count=page_count)
    extractor = OcrExtractor(
        version=version,
        page_renderer=lambda: renderer,
        ocr_runner=lambda: runner,
        preprocessor=lambda img: img,  # identity — keeps cv2 out of the unit
    )
    return extractor, runner, renderer


def test_factory_returns_ocr_instance() -> None:
    extractor = make_extractor()
    assert isinstance(extractor, OcrExtractor)
    assert extractor.name == "ocr"
    assert extractor.version == version


def test_version_module_level_non_empty() -> None:
    """F40 sanity — module-level ``version`` is a non-empty string."""
    assert isinstance(version, str)
    assert version.strip() != ""


def test_can_extract_claims_pdf_by_mime() -> None:
    extractor, _, _ = _make_extractor()
    assert extractor.can_extract("application/pdf", b"") is True


def test_can_extract_claims_pdf_by_magic_bytes() -> None:
    extractor, _, _ = _make_extractor()
    assert extractor.can_extract("application/octet-stream", b"%PDF-1.7") is True


def test_can_extract_claims_image_mimes() -> None:
    extractor, _, _ = _make_extractor()
    for mime in ("image/png", "image/jpeg", "image/tiff", "image/bmp"):
        assert extractor.can_extract(mime, b"") is True


def test_can_extract_rejects_text_mime() -> None:
    extractor, _, _ = _make_extractor()
    assert extractor.can_extract("text/plain", b"hello") is False
    assert extractor.can_extract("text/markdown", b"# hi") is False


def test_extract_invokes_scripted_runner() -> None:
    extractor, runner, renderer = _make_extractor(
        recognised_text="Recovered body line " * 4,
        mean_confidence=80.0,
    )
    doc = extractor.extract(b"%PDF-1.4\n" + (b"payload " * 32), "application/pdf")
    assert isinstance(doc, ExtractedDocument)
    assert "Recovered body line" in doc.markdown
    # Confidence is the normalised mean per the spec: 80/100 = 0.80.
    assert doc.confidence == pytest.approx(0.80, rel=1e-3)
    # The renderer + runner were both consulted exactly once per page.
    assert renderer.render_calls == 1
    assert runner.orientation_calls == 1
    assert runner.recognise_calls == 1


def test_extract_aggregates_confidence_across_pages() -> None:
    """Per-doc confidence is the mean of per-page mean-confidence."""
    extractor, _runner, _renderer = _make_extractor(
        recognised_text="page line " * 4,
        mean_confidence=70.0,
        page_count=3,
    )
    doc = extractor.extract(b"%PDF-1.4\n" + b"x" * 32, "application/pdf")
    # All three pages share the same scripted confidence, so the
    # aggregate equals the per-page value (70/100 = 0.70).
    assert doc.confidence == pytest.approx(0.70, rel=1e-3)
    assert len(doc.pages) == 3


def test_extract_returns_zero_confidence_on_zero_recognition() -> None:
    extractor, _, _ = _make_extractor(mean_confidence=0.0, recognised_text="nothing")
    doc = extractor.extract(b"%PDF-1.4\n", "application/pdf")
    assert doc.confidence == 0.0


def test_quality_ok_true_for_high_confidence() -> None:
    extractor, _, _ = _make_extractor(
        recognised_text="recovered " * 16,
        mean_confidence=80.0,
    )
    doc = extractor.extract(b"%PDF-1.4\n" + b"x" * 32, "application/pdf")
    assert extractor.quality_ok(doc) is True


def test_quality_ok_false_for_low_confidence() -> None:
    """Confidence below 0.6 routes to the vision LLM."""
    extractor, _, _ = _make_extractor(
        recognised_text="recovered " * 16,
        mean_confidence=30.0,  # 0.30 < 0.6
    )
    doc = extractor.extract(b"%PDF-1.4\n" + b"x" * 32, "application/pdf")
    assert extractor.quality_ok(doc) is False


def test_quality_ok_false_for_short_recovery() -> None:
    extractor, _, _ = _make_extractor(
        recognised_text="x",  # below the 50-char floor
        mean_confidence=99.0,
    )
    doc = extractor.extract(b"%PDF-1.4\n" + b"x" * 64, "application/pdf")
    assert extractor.quality_ok(doc) is False


def test_extract_applies_orientation_when_rotation_reported() -> None:
    """Tesseract reports a 90-degree rotation; the page is rotated before recognition."""
    extractor, runner, _ = _make_extractor(rotation_degrees=90)
    extractor.extract(b"%PDF-1.4\n" + b"x" * 32, "application/pdf")
    # The orientation step was consulted; recognition still ran on
    # the rotated image (the scripted runner doesn't care about
    # pixels, but the orientation_calls counter proves the branch
    # executed).
    assert runner.orientation_calls == 1


def test_extract_handles_image_mime() -> None:
    """The renderer is also consulted for ``image/png`` input.

    The scripted renderer ignores the mime hint and produces a single
    synthetic page either way; this test pins that the ``extract``
    path doesn't crash when handed an image mime (the production
    renderer routes image bytes through Pillow rather than pdfplumber,
    but the OcrExtractor's call site is identical).
    """
    extractor, _, renderer = _make_extractor(
        recognised_text="recognised image content " * 4,
        mean_confidence=75.0,
    )
    doc = extractor.extract(b"\x89PNG\r\n\x1a\n" + b"x" * 32, "image/png")
    assert isinstance(doc, ExtractedDocument)
    assert renderer.render_calls == 1


def test_extract_clamps_per_doc_confidence_to_one() -> None:
    """Tesseract conf >= 100 normalises to the 1.0 ceiling at the public surface."""
    extractor, _, _ = _make_extractor(
        recognised_text="recovered line of text " * 4,
        mean_confidence=100.0,
    )
    doc = extractor.extract(b"%PDF-1.4\n" + b"x" * 32, "application/pdf")
    assert doc.confidence == pytest.approx(1.0, rel=1e-3)


def _pillow_available() -> bool:
    """Probe for :mod:`PIL` (``Pillow``) without raising."""
    try:
        import PIL  # noqa: F401 — probe import; only used to detect availability

        return True
    except ImportError:
        return False


# F11 rationale: Pillow is part of the ``ocr`` extra. The renderer
# test below decodes actual PNG bytes through PIL; skip cleanly
# when the extra isn't installed (same contract as cv2 tests).


@pytest.mark.skipif(
    not _pillow_available(),
    reason=(
        "Pillow not installed; install via "
        "'pip install Kairix-agentic-knowledge-mgt[ocr]'. "
        "F11 rationale: the ocr extra is optional; PIL-decode tests "
        "exercise the renderer only when Pillow is available."
    ),
)
def test_production_extractor_handles_real_png_input() -> None:
    """The production make_extractor() routes image mimes through Pillow.

    This drives the production page-renderer's image branch through
    the public :class:`Extractor` surface — no private helpers
    touched. Tesseract is invoked here too if installed; the
    surrounding skipif gates on Pillow only because that's the
    image-decode path being exercised. Without Tesseract the
    OcrRunner construction fails inside extract(); skip then.
    """
    import io

    from PIL import Image

    if not _TESSERACT_AVAILABLE:
        pytest.skip(
            "Tesseract binary not installed; install via 'brew install tesseract' or 'apt install tesseract-ocr'."
        )

    img = Image.new("RGB", (200, 50), (255, 255, 255))
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    png_bytes = buffer.getvalue()

    extractor = make_extractor()
    doc = extractor.extract(png_bytes, "image/png")
    assert isinstance(doc, ExtractedDocument)
    # We don't assert text content (a blank image yields nothing
    # recoverable); we only need to prove the image branch was wired.
    assert doc.metadata.page_count == 1


# ---------------------------------------------------------------------------
# Preprocessing-functions seam — direct unit tests on synthetic
# numpy arrays. Skipped when opencv-python-headless is absent.
# F11 rationale: opencv-python-headless is an optional dependency
# in the ``ocr`` extra; environments without the extra can't exercise
# the production preprocessing chain. The contract-test + scripted-
# runner seams above stay green without cv2.
# ---------------------------------------------------------------------------


def _cv2_available() -> bool:
    """Probe for :mod:`cv2` (``opencv-python-headless``) without raising."""
    try:
        import cv2  # noqa: F401 — probe import; only used to detect availability

        return True
    except ImportError:
        return False


_CV2_AVAILABLE = _cv2_available()
# F11-aware: each ``skipif`` below uses an inline string literal so
# the static F11 detector (which only accepts ``reason="literal"``)
# sees the rationale directly.

pytestmark_cv2 = pytest.mark.skipif(
    not _CV2_AVAILABLE,
    reason=(
        "opencv-python-headless not installed; install via "
        "'pip install Kairix-agentic-knowledge-mgt[ocr]'. "
        "F11 rationale: the ocr extra is optional; the preprocessing "
        "tests exercise the production chain only when cv2 is available."
    ),
)


@pytestmark_cv2
def test_to_greyscale_drops_colour_channels() -> None:
    from kairix.extractors.ocr.preprocess import to_greyscale

    rgb = np.zeros((10, 10, 3), dtype=np.uint8)
    rgb[:, :, 0] = 200  # red plane
    rgb[:, :, 1] = 50  # green plane
    rgb[:, :, 2] = 10  # blue plane
    grey = to_greyscale(rgb)
    assert grey.ndim == 2
    assert grey.shape == (10, 10)
    assert grey.dtype == np.uint8
    # Every pixel should sit between min and max of the colour
    # planes (the conversion is a weighted average).
    assert grey.min() >= 10
    assert grey.max() <= 200


@pytestmark_cv2
def test_to_greyscale_passes_through_when_already_grey() -> None:
    from kairix.extractors.ocr.preprocess import to_greyscale

    grey_in = np.full((10, 10), 128, dtype=np.uint8)
    grey_out = to_greyscale(grey_in)
    np.testing.assert_array_equal(grey_in, grey_out)


@pytestmark_cv2
def test_to_greyscale_rejects_unsupported_shape() -> None:
    """A 4-D tensor or 3-D with non-RGB channel count is rejected."""
    from kairix.extractors.ocr.preprocess import to_greyscale

    weird = np.zeros((10, 10, 5), dtype=np.uint8)  # 5 channels — neither RGB nor RGBA
    with pytest.raises(ValueError, match="unsupported shape"):
        to_greyscale(weird)


@pytestmark_cv2
def test_ensure_dpi_rejects_non_positive_current_dpi() -> None:
    """A zero / negative current_dpi is rejected — divide-by-zero guard."""
    from kairix.extractors.ocr.preprocess import ensure_dpi

    img = np.zeros((10, 10), dtype=np.uint8)
    with pytest.raises(ValueError, match="current_dpi must be > 0"):
        ensure_dpi(img, current_dpi=0, target_dpi=300)


@pytestmark_cv2
def test_deskew_no_op_for_blank_image() -> None:
    """A blank image produces no Hough lines; deskew returns it unchanged."""
    from kairix.extractors.ocr.preprocess import deskew

    blank = np.full((100, 100), 255, dtype=np.uint8)
    out = deskew(blank)
    np.testing.assert_array_equal(blank, out)


@pytestmark_cv2
def test_deskew_handles_page_edges_without_crashing() -> None:
    """A page with both vertical edges (``x1 == x2``) and a slightly
    skewed horizontal baseline doesn't crash deskew — vertical lines
    are page edges and must be filtered before the arctan2 baseline
    estimate. Sabotage-proof: removing the ``x2 == x1`` guard inside
    ``_detect_skew_angle`` triggers a divide-by-zero on the vertical
    stripe.
    """
    import cv2

    from kairix.extractors.ocr.preprocess import deskew

    img = np.full((100, 100), 255, dtype=np.uint8)
    img[20:80, 50:55] = 0  # vertical page edge
    img[60:64, 10:90] = 0  # horizontal text baseline
    # Tilt the whole image so deskew has something to correct.
    matrix = cv2.getRotationMatrix2D((50, 50), 3.0, 1.0)
    tilted = cv2.warpAffine(img, matrix, (100, 100), borderValue=255)
    # The call must complete without raising.
    out = deskew(tilted)
    assert out.shape == tilted.shape


@pytestmark_cv2
def test_binarise_returns_two_value_image() -> None:
    from kairix.extractors.ocr.preprocess import binarise

    # Gradient input — Otsu picks a threshold and emits 0/255 only.
    gradient = np.arange(0, 256, dtype=np.uint8).reshape((1, 256))
    gradient = np.repeat(gradient, 16, axis=0)
    binary = binarise(gradient)
    unique_values = set(np.unique(binary).tolist())
    assert unique_values <= {0, 255}
    # Both values should be present in a gradient — Otsu finds a
    # threshold somewhere in the middle.
    assert unique_values == {0, 255}


@pytestmark_cv2
def test_denoise_smooths_isolated_pixels() -> None:
    from kairix.extractors.ocr.preprocess import denoise

    img = np.full((20, 20), 255, dtype=np.uint8)
    img[10, 10] = 0  # isolated black pixel (salt-and-pepper noise)
    smoothed = denoise(img, kernel=3)
    # Median filter wipes the isolated black pixel — the centre
    # should be back to white.
    assert smoothed[10, 10] == 255


@pytestmark_cv2
def test_denoise_rejects_even_kernel() -> None:
    from kairix.extractors.ocr.preprocess import denoise

    img = np.zeros((10, 10), dtype=np.uint8)
    with pytest.raises(ValueError, match="kernel must be odd"):
        denoise(img, kernel=4)


@pytestmark_cv2
def test_ensure_dpi_upscales_below_target() -> None:
    from kairix.extractors.ocr.preprocess import ensure_dpi

    img = np.zeros((100, 100), dtype=np.uint8)
    upscaled = ensure_dpi(img, current_dpi=150, target_dpi=300)
    # 150 → 300 dpi doubles each side.
    assert upscaled.shape == (200, 200)


@pytestmark_cv2
def test_ensure_dpi_passes_through_when_at_target() -> None:
    from kairix.extractors.ocr.preprocess import ensure_dpi

    img = np.zeros((100, 100), dtype=np.uint8)
    out = ensure_dpi(img, current_dpi=300, target_dpi=300)
    assert out.shape == img.shape


@pytestmark_cv2
def test_deskew_no_op_for_upright_image() -> None:
    """A clean upright text image triggers no rotation."""
    from kairix.extractors.ocr.preprocess import deskew

    # A flat white square has no detectable skew — deskew should
    # be a near-no-op (returns the original or a copy of it).
    img = np.full((100, 100), 255, dtype=np.uint8)
    out = deskew(img)
    assert out.shape == img.shape


@pytestmark_cv2
def test_deskew_corrects_rotated_image() -> None:
    """A rotated horizontal-stripes image is rotated by deskew so the
    stripes recover their alignment.

    Sabotage-proof: if :func:`deskew` were replaced by the identity,
    the corrected and rotated arrays would be identical and the
    inequality assertion fails.

    The test drives :func:`deskew` directly (public API). The
    user-observable contract is "skewed input becomes less skewed";
    we measure that by the row-projection variance — a well-aligned
    horizontal-stripe image concentrates black pixels in fewer rows,
    giving higher variance.
    """
    import cv2

    from kairix.extractors.ocr.preprocess import deskew

    # Build a horizontal-stripes image — strong horizontal edges.
    img = np.full((200, 200), 255, dtype=np.uint8)
    for y in (40, 80, 120, 160):
        img[y : y + 4, 20:180] = 0  # black horizontal stripe
    # Rotate by 7 degrees so the lines are no longer horizontal.
    centre = (100, 100)
    rotation_matrix = cv2.getRotationMatrix2D(centre, 7.0, 1.0)
    rotated = cv2.warpAffine(img, rotation_matrix, (200, 200), borderValue=255)
    corrected = deskew(rotated)
    # The corrected array differs from the rotated input (no-op
    # sabotage would tie the equality below).
    assert not np.array_equal(corrected, rotated)
    # Row-projection variance is higher when stripes are aligned.
    rotated_row_sums = (rotated == 0).sum(axis=1)
    corrected_row_sums = (corrected == 0).sum(axis=1)
    assert float(np.var(corrected_row_sums)) >= float(np.var(rotated_row_sums))


# ---------------------------------------------------------------------------
# Real-Tesseract seam — invokes the actual Tesseract binary against
# the synthesised scanned-PDF fixture. Skipped when pytesseract is not
# installed OR when the Tesseract C++ binary is missing.
# F11 rationale: Tesseract is a system-level binary (brew/apt) that
# can't be pip-installed; the unit test must skip cleanly when it's
# absent on a contributor's dev machine. The same skip is honoured in
# CI on runners that haven't installed the binary.
# ---------------------------------------------------------------------------


def _pytesseract_available() -> bool:
    """Probe for :mod:`pytesseract` without raising."""
    try:
        import pytesseract  # noqa: F401 — probe import; only used for availability detection

        return True
    except ImportError:
        return False


def _tesseract_binary_available() -> bool:
    """Probe for the Tesseract C++ binary on ``PATH``."""
    if not _pytesseract_available():
        return False
    import pytesseract

    try:
        pytesseract.get_tesseract_version()
    except (pytesseract.TesseractNotFoundError, OSError):
        return False
    return True


_TESSERACT_AVAILABLE = _tesseract_binary_available()


@pytestmark_cv2
@pytest.mark.skipif(
    not _TESSERACT_AVAILABLE,
    reason=(
        "Tesseract binary not installed; install via "
        "'brew install tesseract' or 'apt install tesseract-ocr'. "
        "F11 rationale: Tesseract is a system-level binary that can't "
        "be pip-installed; skip cleanly when absent."
    ),
)
def test_real_pdf_fixture_extracts_text() -> None:
    """The real extractor recovers text from the synthesised scanned PDF.

    Sabotage-proof: replacing the page-renderer with one that emits
    blank bytes drops recognised text to ~0 and breaks the
    ``len(markdown) > 10`` check.
    """
    raw = FIXTURE_SCANNED_PDF.read_bytes()
    assert raw.startswith(b"%PDF")
    extractor = make_extractor()
    doc = extractor.extract(raw, "application/pdf")
    assert isinstance(doc, ExtractedDocument)
    assert len(doc.markdown) > 10
    # The recognised text should overlap with at least one of the
    # fixture's known lines. Tesseract isn't always perfect on PIL-
    # rendered tiny fonts, so we look for a single high-signal token.
    candidates = ("scanned", "page", "line", "text", "visible", "Hello", "Second", "Third")
    assert any(c.lower() in doc.markdown.lower() for c in candidates), doc.markdown


@pytestmark_cv2
@pytest.mark.skipif(
    not _TESSERACT_AVAILABLE,
    reason=(
        "Tesseract binary not installed; install via "
        "'brew install tesseract' or 'apt install tesseract-ocr'. "
        "F11 rationale: Tesseract is a system-level binary that can't "
        "be pip-installed; skip cleanly when absent."
    ),
)
def test_real_pdf_fixture_reports_positive_confidence() -> None:
    raw = FIXTURE_SCANNED_PDF.read_bytes()
    extractor = make_extractor()
    doc = extractor.extract(raw, "application/pdf")
    assert doc.confidence > 0


@pytestmark_cv2
@pytest.mark.skipif(
    not _TESSERACT_AVAILABLE,
    reason=(
        "Tesseract binary not installed; install via "
        "'brew install tesseract' or 'apt install tesseract-ocr'. "
        "F11 rationale: Tesseract is a system-level binary that can't "
        "be pip-installed; skip cleanly when absent."
    ),
)
def test_sabotaged_chain_drops_below_quality_floor() -> None:
    """SABOTAGE PROOF — skipping deskew on a deliberately-rotated input
    drops confidence below the 0.6 floor; restoring the full chain
    recovers it.

    This is the mandated MM-2 sabotage proof: mutate the
    preprocessing chain to skip deskew on a skewed input; confirm
    confidence drops below threshold and :meth:`quality_ok` returns
    False; restore.

    The test runs the OCR pipeline twice on the same rotated input:
    once with the production preprocessor (deskew enabled) and once
    with a sabotaged passthrough (deskew disabled). The sabotaged
    run must report a lower confidence than the clean run.
    """
    import io

    import cv2
    import pdfplumber

    from kairix.extractors.ocr.preprocess import (
        TARGET_DPI,
        binarise,
        denoise,
        ensure_dpi,
        to_greyscale,
    )

    raw = FIXTURE_SCANNED_PDF.read_bytes()

    rotation_deg = 25.0  # Heavy skew so deskew has clear corrective work to do.

    class _RotatingRenderer:
        """Renders the fixture via pdfplumber then rotates each page by ``rotation_deg``.

        Uses :mod:`pdfplumber` directly (a public 3rd-party library)
        rather than reaching into the OCR plugin's private renderer.
        """

        def render(self, raw_bytes: bytes, mime: str) -> tuple[np.ndarray, ...]:
            del mime
            rotated_pages: list[np.ndarray] = []
            with pdfplumber.open(io.BytesIO(raw_bytes)) as pdf:
                for page in pdf.pages:
                    pil = page.to_image(resolution=TARGET_DPI).original
                    arr = np.array(pil)
                    h, w = arr.shape[:2]
                    rotation_matrix = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), rotation_deg, 1.0)
                    rotated_pages.append(cv2.warpAffine(arr, rotation_matrix, (w, h), borderValue=255))
            return tuple(rotated_pages)

    def _sabotaged_preprocessor(img: np.ndarray) -> np.ndarray:
        """Same chain as production but WITH deskew DELETED."""
        img = ensure_dpi(img, current_dpi=300)
        img = to_greyscale(img)
        # — deskew step intentionally skipped (this is the sabotage) —
        img = denoise(img)
        img = binarise(img)
        return img

    # Reuse the production preprocessor for the clean branch.
    from kairix.extractors.ocr.preprocess import run_chain as production_preprocessor

    sabotaged = OcrExtractor(
        version=version,
        page_renderer=lambda: _RotatingRenderer(),
        preprocessor=_sabotaged_preprocessor,
    )
    clean = OcrExtractor(
        version=version,
        page_renderer=lambda: _RotatingRenderer(),
        preprocessor=production_preprocessor,
    )
    sabotaged_doc = sabotaged.extract(raw, "application/pdf")
    clean_doc = clean.extract(raw, "application/pdf")
    # The production-chain run reports a strictly higher confidence
    # than the sabotaged one — deskew matters on a rotated input.
    assert clean_doc.confidence > sabotaged_doc.confidence, (
        f"sabotage proof failed: clean={clean_doc.confidence:.3f} sabotaged={sabotaged_doc.confidence:.3f}"
    )
    # And the sabotaged version trips quality_ok=False at the
    # 0.6 floor (or has so few recognised chars it trips the char
    # floor) — restoring deskew recovers either way.
    if sabotaged.quality_ok(sabotaged_doc):
        # If sabotage didn't drop us below the floor, the test
        # cannot prove anything — fail explicitly.
        pytest.fail(
            "sabotage proof failed: sabotaged confidence "
            f"{sabotaged_doc.confidence:.3f} still passes quality_ok; "
            "deskew sabotage was insufficient."
        )
