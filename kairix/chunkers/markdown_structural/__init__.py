"""MarkdownStructuralChunker plugin — heading-aware markdown splitter.

Used by Obsidian, Notion, GitHub READMEs and Google Drive markdown
files (ADR-028 §"Markdown — `MarkdownStructuralChunker`"). Splits on
H1 / H2 / H3 boundaries, writes the heading hierarchy into each
emitted :class:`~kairix.core.protocols.Chunk`'s ``metadata`` so
downstream retrieval can recover the parent path. Sections that exceed
the target budget recurse onto paragraph + sentence fallbacks; overlap
is applied **only within** an oversize section so Topic A never bleeds
into Topic B (matches LangChain's
``MarkdownHeaderTextSplitter -> RecursiveCharacterTextSplitter``
canonical pairing and the Weaviate / LlamaIndex structured-document
recipes cited in ADR-028).

Targets:
  * 512 tokens per chunk (proxy: 2048 characters; 1 token ~ 4 chars).
  * 10-15 % overlap (proxy: 256 chars) applied only inside oversize
    sections — never across heading boundaries.

F55: declares module-level ``version: str``; the
:class:`MarkdownStructuralChunker` instance passes
``chunker_version=self.version`` through to every emitted Chunk.
"""

from __future__ import annotations

from kairix.chunkers.markdown_structural.chunker import (
    PLUGIN_NAME,
    MarkdownStructuralChunker,
)

#: F55-mandated module-level version. Bump on behaviour changes that
#: warrant re-chunking of prior markdown documents (e.g. raising the
#: target budget, changing heading-depth handling, switching the
#: oversize-section fallback). The string flows through the plugin
#: instance into ``Chunk.chunker_version`` so the re-chunk-sweep tick
#: (ADR-028 §"Mechanics") can filter the affected corpus.
version: str = "0.1.0"


def make_chunker() -> MarkdownStructuralChunker:
    """Construct the heading-aware :class:`MarkdownStructuralChunker`.

    The constructor receives ``version=`` from this module's
    :data:`version` so the F55 declaration site stays canonical and
    the class doesn't hard-code the same string.
    """
    return MarkdownStructuralChunker(version=version)


__all__ = [
    "PLUGIN_NAME",
    "MarkdownStructuralChunker",
    "make_chunker",
    "version",
]
