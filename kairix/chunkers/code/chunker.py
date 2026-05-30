"""Language-aware source-code chunker — splits on class / def / func boundaries.

Mirrors LangChain's
``RecursiveCharacterTextSplitter.from_language(language=Language.PYTHON,
chunk_size=1000, chunk_overlap=100)`` recipe (ADR-028 §"GitHub" — the
Databricks chunking guide + Buildmvpfast 2026 references) without the
LangChain dependency.

Algorithm:

  1. Pick the language-aware separator stack for the configured
     ``language`` (Python: ``\\nclass `` / ``\\ndef ``; Go:
     ``\\nfunc `` / ``\\ntype `` / ``\\npackage ``; TypeScript:
     ``\\nfunction `` / ``\\nclass `` / ``\\nexport ``; default:
     generic ``\\n\\n`` / ``\\n``).
  2. Try each separator in order. Split the text on the first
     separator that yields multiple non-trivial pieces; pack those
     pieces greedily into windows of size up to
     :data:`_TARGET_CODE_CHARS`.
  3. If a single piece exceeds the budget, recurse with the next
     finer separator. Hard char-cut is the final fallback.
  4. Apply an overlap of :data:`_CODE_OVERLAP_CHARS` (low — 100
     chars / ~25 tokens) so function signatures aren't duplicated
     across adjacent windows.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from itertools import pairwise

from kairix.core.protocols import Chunk

#: Canonical plugin name surfaced by the entry-point registry.
PLUGIN_NAME = "code"

#: Target chunk size — 1000 chars (~250 tokens). ADR-028 §"GitHub".
_TARGET_CODE_CHARS = 1000

#: Overlap window in characters. Deliberately low (100 chars /
#: ~25 tokens) to avoid duplicating function signatures across
#: adjacent chunks (ADR-028 §"GitHub").
_CODE_OVERLAP_CHARS = 100

#: Metadata key carrying the language hint so downstream retrieval
#: can render syntax-aware previews.
_METADATA_LANGUAGE = "language"


@dataclass(frozen=True)
class _LanguageSpec:
    """Per-language separator stack — coarsest first."""

    language: str
    separators: tuple[str, ...]


#: Per-language separator stacks. Each list is ordered coarsest
#: first; the chunker tries them in order and stops at the first one
#: that splits the text into multiple useful pieces.
_LANGUAGE_SPECS: dict[str, _LanguageSpec] = {
    "python": _LanguageSpec(
        language="python",
        separators=("\nclass ", "\ndef ", "\n\n", "\n", " "),
    ),
    "go": _LanguageSpec(
        language="go",
        separators=("\nfunc ", "\ntype ", "\npackage ", "\n\n", "\n", " "),
    ),
    "typescript": _LanguageSpec(
        language="typescript",
        separators=(
            "\nfunction ",
            "\nclass ",
            "\nexport ",
            "\nconst ",
            "\n\n",
            "\n",
            " ",
        ),
    ),
}

#: Fallback spec for languages without a curated separator stack.
_DEFAULT_SPEC = _LanguageSpec(language="text", separators=("\n\n", "\n", " "))


class CodeChunker:
    """Language-aware :class:`Chunker` for source-code documents."""

    def __init__(self, *, language: str, version: str) -> None:
        """Bind the F55 version, plugin name, and language separator stack."""
        self.name: str = PLUGIN_NAME
        self.version: str = version
        self.language: str = language
        self._spec: _LanguageSpec = _LANGUAGE_SPECS.get(language, _DEFAULT_SPEC)

    def chunk(self, *, text: str, section_kind: str, source_uri: str) -> tuple[Chunk, ...]:
        """Split ``text`` on language-aware boundaries and emit Chunk items.

        ``section_kind`` is accepted for Protocol conformance and held
        live for F19; the code chunker is language-driven irrespective
        of the section discriminator.
        """
        if section_kind:
            del section_kind
        if not text.strip():
            return ()
        windows = _recursive_split(text, self._spec.separators)
        windows_with_overlap = _apply_overlap(windows)
        return tuple(_build_code_chunk(w, source_uri, self.version, self.language) for w in windows_with_overlap)


def _recursive_split(text: str, separators: tuple[str, ...]) -> tuple[str, ...]:
    """Greedy pack text into ``_TARGET_CODE_CHARS`` windows on best-fit separator.

    Walks ``separators`` in order; for each one that splits the text
    into more than one piece, packs the pieces greedily up to budget.
    Pieces still over budget recurse with the next finer separator.
    """
    text = text.strip()
    if not text:
        return ()
    if len(text) <= _TARGET_CODE_CHARS:
        return (text,)
    pieces = _split_on_first_useful_separator(text, separators)
    if pieces is None:
        # No separator split the text at all — hard char-cut.
        return _hard_code_cut(text)
    return _pack_pieces(pieces, separators)


def _split_on_first_useful_separator(text: str, separators: tuple[str, ...]) -> tuple[str, ...] | None:
    """Find the first separator that yields multiple non-empty pieces.

    Returns the split pieces, with the separator prepended to every
    piece except the first (so reassembly round-trips and chunk
    boundaries fall on a meaningful symbol like ``def `` instead of
    cutting it). Returns ``None`` when no separator splits.
    """
    for sep in separators:
        if sep not in text:
            continue
        raw_pieces = text.split(sep)
        if len(raw_pieces) < 2:
            continue
        pieces: list[str] = [raw_pieces[0]]
        pieces.extend(sep.lstrip("\n") + piece for piece in raw_pieces[1:])
        non_empty = tuple(p for p in pieces if p.strip())
        if len(non_empty) >= 2:
            return non_empty
    return None


def _pack_pieces(pieces: tuple[str, ...], separators: tuple[str, ...]) -> tuple[str, ...]:
    """Greedy-pack ``pieces`` into windows ≤ ``_TARGET_CODE_CHARS``.

    Each piece that itself exceeds the budget recurses with the
    remaining (finer) separators.
    """
    next_separators = separators[1:] if len(separators) > 1 else ()
    windows: list[str] = []
    current = ""
    for piece in pieces:
        if len(piece) > _TARGET_CODE_CHARS:
            if current:
                windows.append(current)
                current = ""
            windows.extend(_recursive_split(piece, next_separators))
            continue
        if not current:
            current = piece
            continue
        joined = current + "\n" + piece
        if len(joined) <= _TARGET_CODE_CHARS:
            current = joined
        else:
            windows.append(current)
            current = piece
    if current:
        windows.append(current)
    return tuple(windows)


def _hard_code_cut(text: str) -> tuple[str, ...]:
    """Final fallback — char-stride cut for text with no useful separator."""
    return tuple(text[i : i + _TARGET_CODE_CHARS] for i in range(0, len(text), _TARGET_CODE_CHARS))


def _apply_overlap(windows: tuple[str, ...]) -> tuple[str, ...]:
    """Carry the trailing ``_CODE_OVERLAP_CHARS`` of each window into the next.

    The overlap is deliberately low (100 chars) — code surfaces want
    minimal signature duplication across chunks. The first window is
    emitted as-is; subsequent windows get prefixed with the trailing
    slice of their predecessor.
    """
    if len(windows) <= 1:
        return windows
    out: list[str] = [windows[0]]
    for prev, current in pairwise(windows):
        overlap = prev[-_CODE_OVERLAP_CHARS:] if len(prev) > _CODE_OVERLAP_CHARS else prev
        out.append(overlap + "\n" + current)
    return tuple(out)


def _build_code_chunk(text: str, source_uri: str, chunker_version: str, language: str) -> Chunk:
    """Construct one :class:`Chunk` for a code window.

    Same shape as the markdown chunker — Silver wraps on source_name /
    source_modified_at / sensitivity at the composition site. Language
    is surfaced via ``metadata`` so retrieval can render syntax-aware
    previews.
    """
    return Chunk(
        text=text,
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        source_name="",
        source_uri=source_uri,
        source_modified_at="",
        source_page=None,
        sensitivity="internal",
        chunker_version=chunker_version,
        metadata={_METADATA_LANGUAGE: language},
    )
