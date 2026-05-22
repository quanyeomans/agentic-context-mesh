"""Contract test for the ``ocr`` extractor plugin (F43).

Imports the canonical fake AND the real implementation, then runs the
same :class:`Extractor` Protocol assertions against both. The fake
proves the test seam is real; the real impl proves the production
class satisfies the same shape — without requiring the upstream
:mod:`pytesseract` / :mod:`pdfplumber` / :mod:`opencv-python-headless`
packages to actually run during the contract test.

The real :class:`OcrExtractor` is constructed with scripted
``page_renderer`` + ``ocr_runner`` factories so neither the Tesseract
binary nor any of the OCR extra's Python libraries are invoked in
this layer. The library-level imports are exercised by the unit
tests under ``tests/extractors/`` when the optional ``ocr`` extra
is installed.

Sabotage-proofs:

  * Deleting ``version`` from :mod:`kairix.extractors.ocr` breaks
    ``test_extractor_declares_version``.
  * Flipping ``can_extract`` to ``return True`` for ``text/plain``
    on the real impl breaks ``test_real_rejects_plain_text``.
  * Flipping the quality gate's confidence floor to ``0`` breaks
    ``test_quality_ok_false_on_low_confidence``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pytest

from kairix.extractors import ExtractedDocument, Extractor
from kairix.extractors.ocr import (
    OcrExtractor,
)
from kairix.extractors.ocr import (
    make_extractor as make_real_extractor,
)
from kairix.extractors.ocr import (
    version as ocr_version,
)
from kairix.extractors.ocr.tesseract_runner import (
    OrientationResult,
    RecognitionResult,
)
from tests.fakes import FakeOcrExtractor

pytestmark = pytest.mark.contract


@dataclass
class _ScriptedRecognition:
    """Container for the scripted Tesseract output shape."""

    text: str
    mean_confidence: float


class _ScriptedRunner:
    """Stand-in for :class:`OcrRunner` — returns scripted recognitions."""

    def __init__(self, recognition: _ScriptedRecognition) -> None:
        self._recognition = recognition

    def detect_orientation(self, _img: np.ndarray) -> OrientationResult:
        return OrientationResult(rotation_degrees=0, orientation_confidence=99.0)

    def recognise_text(self, _img: np.ndarray) -> RecognitionResult:
        return RecognitionResult(
            text=self._recognition.text,
            mean_confidence=self._recognition.mean_confidence,
            word_count=len(self._recognition.text.split()),
        )


class _ScriptedRenderer:
    """Stand-in for :class:`PageRenderer` — yields one synthetic page."""

    def __init__(self, *, height: int = 200, width: int = 200) -> None:
        self._height = height
        self._width = width

    def render(self, _raw: bytes, _mime: str) -> tuple[np.ndarray, ...]:
        # A flat white page; the scripted runner produces text
        # independent of pixel content, so the array's value is
        # irrelevant — only shape matters for the orientation step.
        page = np.full((self._height, self._width, 3), 255, dtype=np.uint8)
        return (page,)


def _make_real_with_stubs(
    *,
    recognition_text: str = "recognised body line\n" * 8,
    mean_confidence: float = 85.0,
) -> Extractor:
    """Construct the real :class:`OcrExtractor` with scripted factories.

    The ``preprocessor`` is overridden with an identity lambda so the
    contract test runs in environments without
    :mod:`opencv-python-headless` installed — the real preprocessing
    chain is exercised by the unit tests under
    ``tests/extractors/test_ocr.py`` when the ``ocr`` extra is
    available.
    """
    recognition = _ScriptedRecognition(text=recognition_text, mean_confidence=mean_confidence)
    return OcrExtractor(
        version=ocr_version,
        page_renderer=lambda: _ScriptedRenderer(),
        ocr_runner=lambda: _ScriptedRunner(recognition),
        preprocessor=lambda img: img,
    )


_Factory = Callable[[], Extractor]


@pytest.fixture(
    params=[
        pytest.param(lambda: FakeOcrExtractor(), id="fake"),
        pytest.param(_make_real_with_stubs, id="real"),
    ]
)
def _extractor(request: pytest.FixtureRequest) -> Extractor:
    factory: _Factory = request.param
    return factory()


@pytest.mark.contract
def test_ocr_extractor_satisfies_protocol() -> None:
    """The real factory returns an instance that is a runtime ``Extractor``."""
    real = _make_real_with_stubs()
    assert isinstance(real, Extractor)
    assert isinstance(real, OcrExtractor)


@pytest.mark.contract
def test_extractor_declares_version() -> None:
    """F40 requirement — module-level ``version`` is non-empty."""
    assert isinstance(ocr_version, str)
    assert ocr_version.strip() != ""


@pytest.mark.contract
def test_real_factory_returns_ocr_instance() -> None:
    """``make_extractor`` returns a real :class:`OcrExtractor`."""
    real = make_real_extractor()
    assert isinstance(real, OcrExtractor)
    assert real.name == "ocr"


@pytest.mark.contract
def test_can_extract_claims_pdf(_extractor: Extractor) -> None:
    """Both fake and real claim ``application/pdf``."""
    assert _extractor.can_extract("application/pdf", b"%PDF-1.4") is True


@pytest.mark.contract
def test_can_extract_claims_pdf_by_magic_bytes(_extractor: Extractor) -> None:
    """Magic-byte sniff catches a PDF served as ``application/octet-stream``."""
    assert _extractor.can_extract("application/octet-stream", b"%PDF-1.7") is True


@pytest.mark.contract
def test_can_extract_claims_image_mimes(_extractor: Extractor) -> None:
    """Both fake and real claim PNG / JPEG / TIFF — the OCR plugin's image surface."""
    assert _extractor.can_extract("image/png", b"") is True
    assert _extractor.can_extract("image/jpeg", b"") is True
    assert _extractor.can_extract("image/tiff", b"") is True


@pytest.mark.contract
def test_real_rejects_plain_text() -> None:
    """The real impl refuses ``text/plain`` — that's passthrough's job."""
    real = _make_real_with_stubs()
    assert real.can_extract("text/plain", b"hello") is False


@pytest.mark.contract
def test_extract_returns_document_with_non_empty_markdown(_extractor: Extractor) -> None:
    """``extract`` produces an :class:`ExtractedDocument` with markdown text."""
    doc = _extractor.extract(b"%PDF-1.4\n" + b"x" * 64, "application/pdf")
    assert isinstance(doc, ExtractedDocument)
    assert doc.markdown.strip() != ""


@pytest.mark.contract
def test_quality_ok_true_on_high_confidence_output(_extractor: Extractor) -> None:
    """Quality gate passes when confidence is high enough."""
    doc = _extractor.extract(b"%PDF-1.4\n" + b"x" * 32, "application/pdf")
    assert _extractor.quality_ok(doc) is True


@pytest.mark.contract
def test_quality_ok_false_on_low_confidence() -> None:
    """Quality gate fails when Tesseract reports low confidence."""
    extractor = _make_real_with_stubs(mean_confidence=30.0)
    doc = extractor.extract(b"%PDF-1.4\n" + b"y" * 256, "application/pdf")
    assert extractor.quality_ok(doc) is False
