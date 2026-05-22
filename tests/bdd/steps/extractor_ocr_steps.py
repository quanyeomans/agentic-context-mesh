"""Step definitions for ``extractor_ocr.feature``.

Drives the real :class:`kairix.extractors.ocr.OcrExtractor` with
scripted ``page_renderer=`` and ``ocr_runner=`` factories that
return pre-built numpy arrays + scripted recognition results —
F1-clean (no monkeypatch), F2-clean (no env mutation), no
Tesseract binary invoked. The real upstream Tesseract is exercised
by the contract / unit tests when the operator has installed the
``ocr`` extra plus the Tesseract C++ binary.

Step phrasings carry the literal word "ocr" so the global
pytest-bdd step registry doesn't collide with the markitdown /
passthrough features' analogous Given/When/Then phrases.

Sabotage-proof per step:
  * "claims the mime type" — flipping ``can_extract`` to return
    ``False`` in production fails the step.
  * "carries non-empty markdown" — flipping ``extract`` to return
    empty markdown in production fails the step.
  * "extractor's version string is non-empty" — clearing the
    module-level ``version`` constant in production fails the step.
  * "quality_ok false for the produced document" (escalation gate) —
    flipping ``quality_ok`` to return ``True`` unconditionally fails
    the @error scenario.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pytest
from pytest_bdd import given, parsers, then, when

from kairix.extractors import ExtractedDocument
from kairix.extractors.ocr import OcrExtractor, make_extractor, version
from kairix.extractors.ocr.tesseract_runner import (
    OrientationResult,
    RecognitionResult,
)

pytestmark = pytest.mark.bdd


@dataclass
class _ScriptedRecognition:
    text: str
    mean_confidence: float


class _ScriptedRunner:
    """Fake :class:`OcrRunner` — returns a preconfigured recognition."""

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
    """Fake :class:`PageRenderer` — returns one flat 200x200 RGB page."""

    def render(self, _raw: bytes, _mime: str) -> tuple[np.ndarray, ...]:
        page = np.full((200, 200, 3), 255, dtype=np.uint8)
        return (page,)


@pytest.fixture
def ocr_state() -> dict[str, Any]:
    """Per-scenario state container."""
    return {
        "extractor": None,
        "raw": b"",
        "claimed": None,
        "doc": None,
    }


def _build_extractor(*, recognised_text: str, mean_confidence: float) -> OcrExtractor:
    recognition = _ScriptedRecognition(text=recognised_text, mean_confidence=mean_confidence)
    return OcrExtractor(
        version=version,
        page_renderer=lambda: _ScriptedRenderer(),
        ocr_runner=lambda: _ScriptedRunner(recognition),
        # Identity preprocessor so BDD steps stay green in environments
        # without opencv-python-headless. The real chain is exercised
        # by the unit tests under tests/extractors/test_ocr.py.
        preprocessor=lambda img: img,
    )


@given(parsers.parse('the ocr extractor is registered under the name "{name}"'))
def _register_ocr(ocr_state: dict[str, Any], name: str) -> None:
    real = make_extractor()
    assert isinstance(real, OcrExtractor)
    assert real.name == name
    ocr_state["extractor"] = _build_extractor(
        recognised_text="Recognised body line of text on the scanned page.\n" * 4,
        mean_confidence=85.0,
    )


@given("the operator has raw bytes for a scanned PDF with one page of text")
def _scanned_pdf_bytes(ocr_state: dict[str, Any]) -> None:
    ocr_state["raw"] = b"%PDF-1.4\n" + (b"scan-bytes " * 32)


@given('the operator has raw scanned bytes whose first four bytes are "%PDF"')
def _pdf_magic_bytes(ocr_state: dict[str, Any]) -> None:
    ocr_state["raw"] = b"%PDF-1.4\n%magic-only"


@given("the upstream tesseract runner reports low confidence for the supplied bytes")
def _low_confidence(ocr_state: dict[str, Any]) -> None:
    ocr_state["raw"] = b"%PDF-1.4\n" + (b"noisy-scan " * 32)
    # Below the 0.6 floor: 25/100 -> 0.25 normalised. Recognised text
    # is long enough to clear the char floor so the assertion isolates
    # the confidence branch.
    ocr_state["extractor"] = _build_extractor(
        recognised_text="garbled phantom characters " * 8,
        mean_confidence=25.0,
    )


@when(parsers.parse('the operator asks the ocr extractor whether it can extract mime "{mime}"'))
def _ask_can_extract(ocr_state: dict[str, Any], mime: str) -> None:
    extractor: OcrExtractor = ocr_state["extractor"]
    ocr_state["claimed"] = extractor.can_extract(mime, ocr_state["raw"][:8])


@when("the operator invokes the ocr extractor's extract method on the bytes")
def _invoke_extract(ocr_state: dict[str, Any]) -> None:
    extractor: OcrExtractor = ocr_state["extractor"]
    ocr_state["doc"] = extractor.extract(ocr_state["raw"], "application/pdf")


@then("the ocr extractor claims the mime type")
def _then_claims(ocr_state: dict[str, Any]) -> None:
    assert ocr_state["claimed"] is True


@then("the ocr extractor does not claim the mime type")
def _then_does_not_claim(ocr_state: dict[str, Any]) -> None:
    assert ocr_state["claimed"] is False


@then("the ocr document carries non-empty markdown")
def _then_non_empty(ocr_state: dict[str, Any]) -> None:
    doc: ExtractedDocument = ocr_state["doc"]
    assert isinstance(doc, ExtractedDocument)
    assert doc.markdown.strip() != ""


@then("the ocr extractor reports quality_ok true for the produced document")
def _then_quality_ok_true(ocr_state: dict[str, Any]) -> None:
    extractor: OcrExtractor = ocr_state["extractor"]
    doc: ExtractedDocument = ocr_state["doc"]
    assert extractor.quality_ok(doc) is True


@then("the ocr extractor reports quality_ok false for the produced document")
def _then_quality_ok_false(ocr_state: dict[str, Any]) -> None:
    extractor: OcrExtractor = ocr_state["extractor"]
    doc: ExtractedDocument = ocr_state["doc"]
    assert extractor.quality_ok(doc) is False


@then("the ocr extractor's version string is non-empty")
def _then_version_non_empty(ocr_state: dict[str, Any]) -> None:
    extractor: OcrExtractor = ocr_state["extractor"]
    assert isinstance(extractor.version, str)
    assert extractor.version.strip() != ""
