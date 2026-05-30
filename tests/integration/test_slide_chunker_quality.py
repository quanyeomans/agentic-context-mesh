"""Quality integration tests for :class:`SlideChunker` (ADR-028 Wave G.1).

Seeds a multi-slide deck (text-only / image-only with OCR / mixed
content) and asserts the per-type chunker's quality contract:
  * exactly one chunk per slide,
  * each chunk's metadata carries ``slide_number`` (1-indexed),
  * slide N's content stays out of slide M's chunk (no straddle).

Sabotage-prove targets:
- Drop the per-slide split (``_split_on_slide_headers`` returns one
  whole-text entry regardless): test_one_chunk_per_slide fails →
  restore.
- Strip the ``slide_number`` metadata write: test_metadata_carries_slide_number
  fails → restore.
- Replace per-slide ``slide_text`` slicing with the full document
  text: test_no_content_straddle fails → restore.

EXECUTED sabotage proof: edit ``_split_on_slide_headers`` to
``return [(text.strip(), 1, "")]`` unconditionally and re-run pytest;
test_one_chunk_per_slide reports 1 chunk vs expected 5. Restored.
"""

from __future__ import annotations

import pytest

from kairix.chunkers.slide import SlideChunker, version

pytestmark = pytest.mark.integration


def _five_slide_deck_markdown() -> str:
    """Return a five-slide PptxExtractor-shaped markdown document.

    Slide 1: text-only.
    Slide 2: text + speaker-notes blockquote.
    Slide 3: image-only with OCR'd caption.
    Slide 4: dense bullets.
    Slide 5: mixed content + notes.

    Each slide carries a unique sentinel string so the no-straddle
    assertion can pin "slide N content does not leak into slide M's
    chunk".
    """
    return (
        "## Slide 1: Opening Remarks\n"
        "\n"
        "SENTINEL_ONE — welcome and agenda.\n"
        "\n"
        "## Slide 2: Q3 Architecture\n"
        "\n"
        "SENTINEL_TWO — service mesh decision points.\n"
        "\n"
        "> **Speaker notes**: walk the room through the trade-offs.\n"
        "\n"
        "## Slide 3: System Diagram\n"
        "\n"
        "SENTINEL_THREE — OCR'd image caption: lower-left cluster handles auth.\n"
        "\n"
        "## Slide 4: Risk Register\n"
        "\n"
        "SENTINEL_FOUR — bullet list of mitigations.\n"
        "- mitigation one\n"
        "- mitigation two\n"
        "- mitigation three\n"
        "\n"
        "## Slide 5: Next Steps\n"
        "\n"
        "SENTINEL_FIVE — actions for the next sprint.\n"
        "\n"
        "> **Speaker notes**: confirm owners before close-out.\n"
    )


def test_one_chunk_per_slide() -> None:
    """Five slides in → exactly five chunks out."""
    chunker = SlideChunker()
    chunks = chunker.chunk(
        text=_five_slide_deck_markdown(),
        section_kind="text",
        source_uri="agent-alpha-deck.pptx",
    )
    assert len(chunks) == 5


def test_metadata_carries_slide_number() -> None:
    """Every chunk's metadata carries ``slide_number`` 1-indexed."""
    chunker = SlideChunker()
    chunks = chunker.chunk(
        text=_five_slide_deck_markdown(),
        section_kind="text",
        source_uri="agent-alpha-deck.pptx",
    )
    slide_numbers = [c.metadata["slide_number"] for c in chunks]
    assert slide_numbers == ["1", "2", "3", "4", "5"]
    # source_page mirrors slide_number for retrieval citation paths.
    assert [c.source_page for c in chunks] == [1, 2, 3, 4, 5]


def test_no_content_straddle_slide_three_text_not_in_slide_two_chunk() -> None:
    """Slide 3's sentinel must not appear in slide 2's chunk."""
    chunker = SlideChunker()
    chunks = chunker.chunk(
        text=_five_slide_deck_markdown(),
        section_kind="text",
        source_uri="agent-alpha-deck.pptx",
    )
    slide_two = chunks[1]
    assert "SENTINEL_TWO" in slide_two.text
    assert "SENTINEL_THREE" not in slide_two.text
    assert "SENTINEL_ONE" not in slide_two.text


def test_each_chunk_owns_its_own_sentinel() -> None:
    """Each slide's sentinel appears only in that slide's chunk."""
    chunker = SlideChunker()
    chunks = chunker.chunk(
        text=_five_slide_deck_markdown(),
        section_kind="text",
        source_uri="agent-alpha-deck.pptx",
    )
    sentinels = ["SENTINEL_ONE", "SENTINEL_TWO", "SENTINEL_THREE", "SENTINEL_FOUR", "SENTINEL_FIVE"]
    for i, sentinel in enumerate(sentinels):
        assert sentinel in chunks[i].text
        for j, other_chunk in enumerate(chunks):
            if j == i:
                continue
            assert sentinel not in other_chunk.text


def test_chunks_carry_slide_title_metadata() -> None:
    """Slide title is preserved in ``metadata["slide_title"]``."""
    chunker = SlideChunker()
    chunks = chunker.chunk(
        text=_five_slide_deck_markdown(),
        section_kind="text",
        source_uri="agent-alpha-deck.pptx",
    )
    titles = [c.metadata["slide_title"] for c in chunks]
    assert titles == [
        "Opening Remarks",
        "Q3 Architecture",
        "System Diagram",
        "Risk Register",
        "Next Steps",
    ]


def test_chunker_version_flows_through() -> None:
    """F55 propagation — sanity-check on the integration-quality surface."""
    chunker = SlideChunker()
    chunks = chunker.chunk(
        text=_five_slide_deck_markdown(),
        section_kind="text",
        source_uri="agent-alpha-deck.pptx",
    )
    for c in chunks:
        assert c.chunker_version == version
