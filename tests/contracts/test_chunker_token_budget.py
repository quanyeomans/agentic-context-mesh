"""F99 contract: text chunkers never emit a chunk over the embedding budget.

Guards the oversized-chunk bug class (kairix#624): a chunk over the
text-embedding-3-large 8191-token limit silently truncates at embed time, so its
tail never reaches retrieval. The chunkers that take arbitrary free text
(MarkdownStructuralChunker on the cutover path + the ParagraphFallbackChunker the
registry returns for any unregistered type) MUST bound their output regardless of
input size.

tiktoken is not a kairix dependency, so this uses the same char proxy as
``chunk_stats`` (8191 tokens x ~4 chars = 32764 chars) — a conservative ceiling.

Scope: free-text chunkers only. The page/unit chunkers (slide/sheet/docx/
calendar/email/thread) chunk by inherent structural units and need format-specific
oversized fixtures to exercise — covered separately (see Linear PLA-229).
"""

from __future__ import annotations

import pytest

from kairix.core.connectors.chunker_registry import MARKDOWN_MIME, build_default_registry

pytestmark = pytest.mark.contract

# 8191-token embed limit at the production ~4 chars/token density.
_EMBED_CHAR_BUDGET = 8191 * 4

# Pathological input: a structured doc whose body is a single unsplittable
# 400K-char run (no paragraph/sentence/word boundaries) PLUS a huge glued
# paragraph — forces every chunker's hard-cut fallback to fire.
_OVERSIZED_TEXT = "# Title\n\n" + ("lorem ipsum dolor sit amet consectetur " * 12000) + "\n\n" + ("x" * 400000)


def _text_chunkers() -> dict[str, object]:
    registry = build_default_registry()
    markdown = registry.dispatch(kind="obsidian", mime=MARKDOWN_MIME, section_kind="text")
    fallback = registry.dispatch(kind="__unregistered__", mime="__none__", section_kind="text")
    return {
        f"markdown_structural@{getattr(markdown, 'version', '?')}": markdown,
        f"paragraph_fallback@{getattr(fallback, 'version', '?')}": fallback,
    }


_CHUNKERS = _text_chunkers()


@pytest.mark.parametrize("chunker", list(_CHUNKERS.values()), ids=list(_CHUNKERS.keys()))
def test_text_chunker_bounds_chunk_size(chunker: object) -> None:
    chunks = chunker.chunk(text=_OVERSIZED_TEXT, section_kind="text", source_uri="src://oversized")  # type: ignore[attr-defined]  # chunk() is a Chunker-protocol method present on every registry chunker at runtime
    assert chunks, "chunker produced no chunks for a large input"
    oversized = [len(c.text) for c in chunks if len(c.text) > _EMBED_CHAR_BUDGET]
    assert not oversized, (
        f"{type(chunker).__name__} emitted {len(oversized)} chunk(s) over the "
        f"{_EMBED_CHAR_BUDGET}-char embed budget (largest {max(oversized) if oversized else 0} chars); "
        "chunks over the 8191-token limit truncate silently at embed time."
    )
