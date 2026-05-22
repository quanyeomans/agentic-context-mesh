"""Contract test for the ``pptx`` extractor plugin (F43).

Imports the canonical fake AND the real implementation, then runs the
same :class:`Extractor` Protocol assertions against both. The fake
proves the test seam is real; the real impl proves the production
class satisfies the same shape — without requiring the upstream
``python-pptx`` library to be present in the contract-test environment.

The real :class:`PptxExtractor` is constructed with a scripted
``presentation_loader`` so the upstream library is not imported during
the contract test. The library-level import is exercised by the unit
tests under ``tests/extractors/`` when the optional ``pptx`` extra is
installed.

Sabotage-proofs:

  * Deleting ``version`` from :mod:`kairix.extractors.pptx` breaks
    ``test_extractor_declares_version``.
  * Flipping ``can_extract`` to ``return True`` for ``text/plain`` on
    the real impl breaks ``test_real_rejects_plain_text``.
  * Flipping the quality gate's char threshold to ``0`` breaks
    ``test_quality_ok_false_on_short_output``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pytest

from kairix.extractors import ExtractedDocument, Extractor
from kairix.extractors.pptx import (
    PptxExtractor,
)
from kairix.extractors.pptx import (
    make_extractor as make_real_extractor,
)
from kairix.extractors.pptx import (
    version as pptx_version,
)
from tests.fakes import FakePptxExtractor

pytestmark = pytest.mark.contract


_PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


@dataclass
class _StubShapes:
    """Minimal stub of ``slide.shapes`` — iterable of text shapes plus title."""

    shapes_list: list[Any] = field(default_factory=list)
    title_shape: Any = None

    def __iter__(self) -> Any:
        return iter(self.shapes_list)

    @property
    def title(self) -> Any:
        return self.title_shape


@dataclass
class _StubTextShape:
    text: str
    has_text_frame: bool = True
    shape_type: int = 17  # text box

    @property
    def text_frame(self) -> Any:
        return self


@dataclass
class _StubTitleShape:
    text: str
    has_text_frame: bool = True
    shape_type: int = 14  # placeholder

    @property
    def text_frame(self) -> Any:
        return self


@dataclass
class _StubNotesTextFrame:
    text: str


@dataclass
class _StubNotesSlide:
    notes_text_frame: _StubNotesTextFrame


@dataclass
class _StubSlide:
    shapes: _StubShapes
    notes_slide: _StubNotesSlide | None


@dataclass
class _StubCoreProperties:
    title: str = "Scripted Deck"
    author: str = "agent-alpha"
    created: datetime | None = None


@dataclass
class _StubPresentation:
    slides: list[_StubSlide]
    core_properties: _StubCoreProperties = field(default_factory=_StubCoreProperties)


def _make_stub_presentation(*, slide_count: int = 3, with_notes: bool = True) -> _StubPresentation:
    slides: list[_StubSlide] = []
    for i in range(slide_count):
        n = i + 1
        title = _StubTitleShape(text=f"Scripted Slide {n}")
        body = _StubTextShape(text=f"Body text for slide {n}")
        shapes = _StubShapes(shapes_list=[title, body], title_shape=title)
        notes_slide: _StubNotesSlide | None = None
        if with_notes:
            notes_slide = _StubNotesSlide(notes_text_frame=_StubNotesTextFrame(text=f"Notes for slide {n}"))
        slides.append(_StubSlide(shapes=shapes, notes_slide=notes_slide))
    return _StubPresentation(slides=slides)


def _make_real_with_stub(*, slide_count: int = 3, with_notes: bool = True) -> Extractor:
    """Construct the real :class:`PptxExtractor` with a stub loader."""
    stub = _make_stub_presentation(slide_count=slide_count, with_notes=with_notes)
    return PptxExtractor(
        version=pptx_version,
        presentation_loader=lambda _path: stub,
    )


_Factory = Callable[[], Extractor]


@pytest.fixture(
    params=[
        pytest.param(lambda: FakePptxExtractor(), id="fake"),
        pytest.param(_make_real_with_stub, id="real"),
    ]
)
def _extractor(request: pytest.FixtureRequest) -> Extractor:
    factory: _Factory = request.param
    return factory()


@pytest.mark.contract
def test_pptx_extractor_satisfies_protocol() -> None:
    """The real factory returns an instance that is a runtime ``Extractor``."""
    real = _make_real_with_stub()
    assert isinstance(real, Extractor)
    assert isinstance(real, PptxExtractor)


@pytest.mark.contract
def test_extractor_declares_version() -> None:
    """F40 requirement — module-level ``version`` is non-empty."""
    assert isinstance(pptx_version, str)
    assert pptx_version.strip() != ""


@pytest.mark.contract
def test_real_factory_returns_pptx_instance() -> None:
    """``make_extractor`` returns a real :class:`PptxExtractor`."""
    real = make_real_extractor()
    assert isinstance(real, PptxExtractor)
    assert real.name == "pptx"


@pytest.mark.contract
def test_can_extract_claims_pptx_mime(_extractor: Extractor) -> None:
    """Both fake and real claim the Office Open XML presentation mime."""
    assert _extractor.can_extract(_PPTX_MIME, b"PK\x03\x04") is True


@pytest.mark.contract
def test_real_rejects_plain_text() -> None:
    """The real impl refuses ``text/plain`` — that's passthrough's job."""
    real = _make_real_with_stub()
    assert real.can_extract("text/plain", b"hello") is False


@pytest.mark.contract
def test_real_rejects_bare_zip_without_presentation_mime() -> None:
    """ZIP magic alone is ambiguous (could be DOCX / XLSX / JAR)."""
    real = _make_real_with_stub()
    assert real.can_extract("application/octet-stream", b"PK\x03\x04") is False


@pytest.mark.contract
def test_extract_returns_document_with_non_empty_markdown(_extractor: Extractor) -> None:
    """``extract`` produces an :class:`ExtractedDocument` with markdown text."""
    doc = _extractor.extract(b"PK\x03\x04" + b"x" * 256, _PPTX_MIME)
    assert isinstance(doc, ExtractedDocument)
    assert doc.markdown.strip() != ""
    assert len(doc.pages) > 0


@pytest.mark.contract
def test_quality_ok_true_on_substantive_output(_extractor: Extractor) -> None:
    """Quality gate passes when markdown has enough content."""
    doc = _extractor.extract(b"PK\x03\x04" + b"x" * 64, _PPTX_MIME)
    assert _extractor.quality_ok(doc) is True


@pytest.mark.contract
def test_quality_ok_false_on_short_output() -> None:
    """Quality gate fails when the loader returns near-empty content."""
    extractor = _make_real_with_stub(slide_count=0)
    doc = extractor.extract(b"PK\x03\x04" + b"y" * 4096, _PPTX_MIME)
    assert extractor.quality_ok(doc) is False
