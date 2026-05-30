"""CodeChunker plugin — language-aware source-code splitter.

Used by the GitHub connector for Python / Go / TypeScript source
files (ADR-028 §"GitHub — `CodeChunker` + `MarkdownStructuralChunker`").
Splits preferentially on language-aware boundaries (``\\nclass `` and
``\\ndef `` for Python; ``\\nfunc `` for Go; ``\\nfunction `` /
``\\nexport `` / ``\\nclass `` for TypeScript) before falling back to
generic separators. Mirrors the LangChain
``RecursiveCharacterTextSplitter.from_language(...)`` recipe cited in
ADR-028 — without dragging the LangChain dependency tree along.

Targets:
  * 1000 chars (~250 tokens) per chunk.
  * 100 chars overlap (~25 tokens) — deliberately low so function
    signatures don't get duplicated across adjacent chunks.

F55: declares module-level ``version: str``; the
:class:`CodeChunker` instance passes ``chunker_version=self.version``
through to every emitted Chunk.
"""

from __future__ import annotations

from kairix.chunkers.code.chunker import PLUGIN_NAME, CodeChunker

#: F55-mandated module-level version. Bump on behaviour changes
#: that warrant re-chunking of prior code documents (e.g. adding a
#: new language separator set, changing the budget, swapping the
#: tokeniser proxy).
version: str = "0.1.0"


def make_chunker(*, language: str = "python") -> CodeChunker:
    """Construct the language-aware :class:`CodeChunker`.

    ``language`` selects the separator set; defaults to ``"python"``
    so a no-arg ``make_chunker()`` is useful out of the box. The
    constructor receives ``version=`` from this module's
    :data:`version` so the F55 declaration site stays canonical.
    """
    return CodeChunker(language=language, version=version)


__all__ = [
    "PLUGIN_NAME",
    "CodeChunker",
    "make_chunker",
    "version",
]
