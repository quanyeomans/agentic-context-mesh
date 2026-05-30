"""Step definitions for ``chunker_slide.feature`` (ADR-028 Wave G.1).

Drives the real :class:`kairix.chunkers.slide.SlideChunker` directly
(no extractor in the loop — the chunker is the unit under test).
The scripted deck markdown matches the shape PptxExtractor produces,
so the chunker exercises its production split logic on production-shaped
input.

Sabotage-proofs per step:
  * "emits one chunk per slide" — flipping
    :func:`_split_on_slide_headers` to a one-entry list fails the step.
  * "carries the slide number in its metadata" — dropping the
    ``slide_number`` metadata write in :func:`_build_slide_chunk`
    fails the step.
  * "emits no chunks" — removing the empty-input guard fails the
    @error scenario.
"""

from __future__ import annotations

from typing import Any

import pytest
from pytest_bdd import given, then, when

from kairix.chunkers.slide import SlideChunker

pytestmark = pytest.mark.bdd


@pytest.fixture
def slide_state() -> dict[str, Any]:
    """Per-scenario state container."""
    return {
        "chunker": None,
        "deck_markdown": "",
        "chunks": (),
    }


def _scripted_three_slide_deck_markdown() -> str:
    return (
        "## Slide 1: Opening\n\n"
        "alpha-body-one\n\n"
        "## Slide 2: Architecture\n\n"
        "alpha-body-two\n\n"
        "## Slide 3: Wrap-Up\n\n"
        "alpha-body-three\n"
    )


@given("the slide chunker is constructed")
def _construct_slide(slide_state: dict[str, Any]) -> None:
    slide_state["chunker"] = SlideChunker()


@given("the operator has a scripted three slide deck markdown")
def _scripted_deck(slide_state: dict[str, Any]) -> None:
    slide_state["deck_markdown"] = _scripted_three_slide_deck_markdown()


@when("the operator invokes the slide chunker on the deck markdown")
def _invoke_on_deck(slide_state: dict[str, Any]) -> None:
    chunker: SlideChunker = slide_state["chunker"]
    slide_state["chunks"] = chunker.chunk(
        text=slide_state["deck_markdown"],
        section_kind="text",
        source_uri="agent-alpha-deck.pptx",
    )


@when("the operator invokes the slide chunker on empty text")
def _invoke_on_empty(slide_state: dict[str, Any]) -> None:
    chunker: SlideChunker = slide_state["chunker"]
    slide_state["chunks"] = chunker.chunk(
        text="",
        section_kind="text",
        source_uri="agent-alpha-deck.pptx",
    )


@then("the slide chunker emits one chunk per slide")
def _one_chunk_per_slide(slide_state: dict[str, Any]) -> None:
    assert len(slide_state["chunks"]) == 3


@then("each chunk carries the slide number in its metadata")
def _slide_number_metadata(slide_state: dict[str, Any]) -> None:
    expected = ["1", "2", "3"]
    actual = [c.metadata["slide_number"] for c in slide_state["chunks"]]
    assert actual == expected


@then("the slide chunker emits no chunks")
def _no_chunks(slide_state: dict[str, Any]) -> None:
    assert slide_state["chunks"] == ()
