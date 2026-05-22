"""Step definitions for ``extractor_pptx.feature`` (OF-1).

Drives the real :class:`kairix.extractors.pptx.PptxExtractor` with a
fake ``presentation_loader`` that returns a scripted three-slide
presentation — F1-clean (no monkeypatch), F2-clean (no env mutation).
The fake loader is the canonical seam for behaviour tests; the real
upstream library is exercised by the contract / unit / integration
tests instead.

Step phrasings carry the literal word "pptx" so the global pytest-bdd
step registry doesn't collide with the markitdown / passthrough
features' analogous phrases.

Sabotage-proof per step:
  * "claims the mime type" — flipping ``can_extract`` to return
    ``False`` in production fails the step.
  * "carries one page per slide" — flipping :meth:`extract` to drop
    the per-slide loop in production fails the step.
  * "contains the speaker notes blockquote" — removing the notes
    branch in :func:`_slide_to_markdown` fails the step.
  * "extractor's version string is non-empty" — clearing the
    module-level ``version`` constant in production fails the step.
  * "quality_ok false for the produced document" — flipping
    :meth:`quality_ok` to return ``True`` unconditionally fails the
    @error scenario.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.extractors import ExtractedDocument
from kairix.extractors.pptx import PptxExtractor, make_extractor, version

pytestmark = pytest.mark.bdd


# Repeated mime constant — lifted to a module-level name so the
# >=10-char literal doesn't recur >=3 times (F17 guard).
_PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


# ---------------------------------------------------------------------------
# Scripted presentation — matches the wire-shape Protocol production
# reads off ``pptx.Presentation``.
# ---------------------------------------------------------------------------


@dataclass
class _Shapes:
    shapes_list: list[Any] = field(default_factory=list)
    title_shape: Any = None

    def __iter__(self) -> Any:
        return iter(self.shapes_list)

    @property
    def title(self) -> Any:
        return self.title_shape


@dataclass
class _TextShape:
    text: str
    has_text_frame: bool = True
    shape_type: int = 17

    @property
    def text_frame(self) -> Any:
        return self


@dataclass
class _TitleShape:
    text: str
    has_text_frame: bool = True
    shape_type: int = 14

    @property
    def text_frame(self) -> Any:
        return self


@dataclass
class _NotesTextFrame:
    text: str


@dataclass
class _NotesSlide:
    notes_text_frame: _NotesTextFrame


@dataclass
class _Slide:
    shapes: _Shapes
    notes_slide: _NotesSlide | None = None


@dataclass
class _CoreProperties:
    title: str = "Scripted Deck"
    author: str = "agent-alpha"
    created: Any = None


@dataclass
class _Presentation:
    slides: list[_Slide]
    core_properties: _CoreProperties = field(default_factory=_CoreProperties)


def _scripted_three_slide_deck() -> _Presentation:
    slides: list[_Slide] = []
    for i in range(3):
        n = i + 1
        title = _TitleShape(text=f"Scripted Slide {n}")
        body = _TextShape(text=f"Body text for slide {n}")
        shapes = _Shapes(shapes_list=[title, body], title_shape=title)
        notes = _NotesSlide(notes_text_frame=_NotesTextFrame(text=f"Speaker line {n}."))
        slides.append(_Slide(shapes=shapes, notes_slide=notes))
    return _Presentation(slides=slides)


def _empty_deck() -> _Presentation:
    return _Presentation(slides=[])


@pytest.fixture
def pptx_state() -> dict[str, Any]:
    """Per-scenario state container."""
    return {
        "extractor": None,
        "raw": b"",
        "claimed": None,
        "doc": None,
    }


def _build_extractor(presentation: _Presentation) -> PptxExtractor:
    return PptxExtractor(
        version=version,
        presentation_loader=lambda _path: presentation,
    )


# ---------------------------------------------------------------------------
# Given
# ---------------------------------------------------------------------------


@given(parsers.parse('the pptx extractor is registered under the name "{name}"'))
def _register_pptx(pptx_state: dict[str, Any], name: str) -> None:
    real = make_extractor()
    assert isinstance(real, PptxExtractor)
    assert real.name == name
    # Default state carries the scripted 3-slide deck; @error scenarios
    # override with their own deck below.
    pptx_state["extractor"] = _build_extractor(_scripted_three_slide_deck())
    pptx_state["raw"] = b"PK\x03\x04" + (b"x" * 256)


@given("the operator has a scripted three slide presentation")
def _three_slide_deck(pptx_state: dict[str, Any]) -> None:
    pptx_state["extractor"] = _build_extractor(_scripted_three_slide_deck())
    pptx_state["raw"] = b"PK\x03\x04" + (b"x" * 256)


@given("the operator has a scripted empty presentation")
def _empty_deck_given(pptx_state: dict[str, Any]) -> None:
    pptx_state["extractor"] = _build_extractor(_empty_deck())
    pptx_state["raw"] = b"PK\x03\x04" + (b"x" * 1024)


# ---------------------------------------------------------------------------
# When
# ---------------------------------------------------------------------------


@when(parsers.parse('the operator asks the pptx extractor whether it can extract mime "{mime}"'))
def _ask_can_extract(pptx_state: dict[str, Any], mime: str) -> None:
    extractor: PptxExtractor = pptx_state["extractor"]
    pptx_state["claimed"] = extractor.can_extract(mime, pptx_state["raw"][:8])


@when("the operator asks the pptx extractor whether it can extract the office open xml presentation mime")
def _ask_pptx_mime(pptx_state: dict[str, Any]) -> None:
    extractor: PptxExtractor = pptx_state["extractor"]
    pptx_state["claimed"] = extractor.can_extract(_PPTX_MIME, b"PK\x03\x04")


@when("the operator invokes the pptx extractor's extract method on the bytes")
def _invoke_extract(pptx_state: dict[str, Any]) -> None:
    extractor: PptxExtractor = pptx_state["extractor"]
    pptx_state["doc"] = extractor.extract(pptx_state["raw"], _PPTX_MIME)


# ---------------------------------------------------------------------------
# Then
# ---------------------------------------------------------------------------


@then("the pptx extractor claims the mime type")
def _then_claims(pptx_state: dict[str, Any]) -> None:
    assert pptx_state["claimed"] is True


@then("the pptx extractor does not claim the mime type")
def _then_does_not_claim(pptx_state: dict[str, Any]) -> None:
    assert pptx_state["claimed"] is False


@then("the pptx document carries one page per slide")
def _then_one_page_per_slide(pptx_state: dict[str, Any]) -> None:
    doc: ExtractedDocument = pptx_state["doc"]
    assert isinstance(doc, ExtractedDocument)
    assert len(doc.pages) == 3


@then("the pptx document carries each slide title in the page text")
def _then_titles_in_pages(pptx_state: dict[str, Any]) -> None:
    doc: ExtractedDocument = pptx_state["doc"]
    for i, page in enumerate(doc.pages):
        assert f"Scripted Slide {i + 1}" in page.text


@then("the pptx document markdown contains the speaker notes blockquote")
def _then_notes_in_markdown(pptx_state: dict[str, Any]) -> None:
    doc: ExtractedDocument = pptx_state["doc"]
    assert "> **Speaker notes**: Speaker line 1." in doc.markdown


@then("the pptx extractor reports quality_ok true for the produced document")
def _then_quality_ok_true(pptx_state: dict[str, Any]) -> None:
    extractor: PptxExtractor = pptx_state["extractor"]
    doc: ExtractedDocument = pptx_state["doc"]
    assert extractor.quality_ok(doc) is True


@then("the pptx extractor reports quality_ok false for the produced document")
def _then_quality_ok_false(pptx_state: dict[str, Any]) -> None:
    extractor: PptxExtractor = pptx_state["extractor"]
    doc: ExtractedDocument = pptx_state["doc"]
    assert extractor.quality_ok(doc) is False


@then("the pptx extractor's version string is non-empty")
def _then_version_non_empty(pptx_state: dict[str, Any]) -> None:
    extractor: PptxExtractor = pptx_state["extractor"]
    assert isinstance(extractor.version, str)
    assert extractor.version.strip() != ""
