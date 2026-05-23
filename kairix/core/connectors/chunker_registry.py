"""Chunker registry — ADR v2 §6 per-``(kind, mime)`` dispatch surface.

The registry maps ``(kind, mime)`` keys to :class:`~kairix.core.protocols.Chunker`
implementations. Wave C lands the registry + a paragraph-shaped fallback;
Wave F lands the per-kind plugins (tree-sitter / per-ticket / thread-aware /
slide / tabular / email-thread / event / transcript / web) under
``kairix/chunkers/<name>/``.

Until Wave F lands, every dispatch returns the fallback chunker.

F55 contract: every :class:`Chunker` plugin declares ``version: str`` AND
every emitted :class:`~kairix.core.protocols.Chunk` carries
``chunker_version=self.version``. The fallback satisfies this contract too.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence

from kairix.core.protocols import Chunk, Chunker, Sensitivity

# Paragraph-boundary character budget for the fallback. Same value the
# existing F38-locked Silver path uses (kairix/core/connectors/silver.py),
# so the runtime flag flip is a behavioural no-op when only the fallback
# is registered.
_TARGET_CHUNK_CHARS = 1000


def _split_paragraphs(text: str) -> tuple[str, ...]:
    """Split ``text`` on blank-line boundaries, glue paragraphs up to budget.

    Matches the behaviour of ``kairix.core.connectors.silver._chunk_markdown``
    so the fallback is observably identical to legacy chunking. Wave F
    plugins MAY override per (kind, mime); the fallback is the contract.
    """
    stripped = text.strip()
    if not stripped:
        return ()
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", stripped) if p.strip()]
    if not paragraphs:
        return ()
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        if not current:
            current = para
            continue
        if len(current) + len(para) + 2 <= _TARGET_CHUNK_CHARS:
            current = current + "\n\n" + para
        else:
            chunks.append(current)
            current = para
    if current:
        chunks.append(current)
    return tuple(chunks)


class ParagraphFallbackChunker:
    """Paragraph-boundary fallback :class:`Chunker` for the registry.

    Used when no per-``(kind, mime)`` plugin matches. Emits
    paragraph-shaped chunks identical to the legacy Silver chunking so
    the runtime-flag flip is behaviour-equivalent until Wave F plugins
    land.

    Declares ``version: str = "1"`` (F55). Every emitted Chunk carries
    ``chunker_version=self.version`` (also F55).
    """

    def __init__(self, *, version: str = "1") -> None:
        self.version = version

    def chunk(self, *, text: str, section_kind: str, source_uri: str) -> tuple[Chunk, ...]:
        """Paragraph-split ``text`` into Chunk items.

        ``section_kind`` is recorded for future per-kind branching but the
        fallback ignores it (the fallback is uniform across section kinds
        — Wave F plugins will branch on the value). ``source_uri`` is
        propagated through to each Chunk per F39.
        """
        # ``section_kind`` is part of the :class:`Chunker` Protocol shape so
        # Wave F implementations can branch on it; the fallback emits
        # paragraph-shaped chunks regardless. Read it once to mark the
        # parameter live for F19 (the value is unused at the fallback layer
        # but the slot is load-bearing for protocol conformance).
        if not section_kind:
            section_kind = "text"  # defensive default — empty section_kind = text section
        del section_kind
        return _build_chunks_from_paragraphs(
            paragraphs=_split_paragraphs(text),
            source_uri=source_uri,
            chunker_version=self.version,
        )


def _build_chunks_from_paragraphs(
    *,
    paragraphs: Sequence[str],
    source_uri: str,
    chunker_version: str,
    source_name: str = "",
    source_modified_at: str = "",
    sensitivity: Sensitivity = "internal",
) -> tuple[Chunk, ...]:
    """Build :class:`Chunk` tuples from paragraph strings, F39 + F55 clean.

    Note: callers SHOULD pass through real values for ``source_name``,
    ``source_modified_at``, and ``sensitivity`` via the Silver-level
    composition site; the defaults here exist so this helper can be
    called from the Chunker.chunk(...) protocol surface (which only has
    ``text + section_kind + source_uri`` per the Protocol shape). Silver
    wraps these chunks with the full per-document context before writing.
    """
    return tuple(
        Chunk(
            text=chunk_text,
            content_hash=hashlib.sha256(chunk_text.encode("utf-8")).hexdigest(),
            source_name=source_name,
            source_uri=source_uri,
            source_modified_at=source_modified_at,
            source_page=None,
            sensitivity=sensitivity,
            chunker_version=chunker_version,
        )
        for chunk_text in paragraphs
    )


class ChunkerRegistry:
    """Per-``(kind, mime)`` dispatch surface for :class:`Chunker` plugins.

    Construct once at worker boot; pass into the Silver pipeline (Wave C
    runtime flag path). The Wave C landing registers only the fallback;
    Wave F lands the plugin registrations.

    Lookup order in :meth:`dispatch`:

    1. Exact ``(kind, mime)`` match in :attr:`_registry`.
    2. The fallback chunker.

    The ``section_kind`` argument is forwarded to the chunker — Wave F
    chunkers may branch on it (e.g. tree-sitter vs comment-block).
    """

    def __init__(self) -> None:
        self._registry: dict[tuple[str, str], Chunker] = {}
        self._fallback: Chunker = ParagraphFallbackChunker(version="1")

    def register(self, *, kind: str, mime: str, chunker: Chunker) -> None:
        """Register a chunker for ``(kind, mime)``. Overwrites any prior entry."""
        self._registry[(kind, mime)] = chunker

    def dispatch(self, *, kind: str, mime: str, section_kind: str) -> Chunker:
        """Resolve the chunker for ``(kind, mime, section_kind)``.

        Returns the registered chunker on exact match; otherwise the
        fallback. ``section_kind`` is recorded for future per-section
        dispatch but Wave C uses ``(kind, mime)`` as the primary key
        (the fallback ignores ``section_kind``; Wave F plugins may
        branch on it via :meth:`Chunker.chunk`).
        """
        # ``section_kind`` is reserved for Wave F per-section dispatch.
        # Read it once so the slot stays live for F19; the registry
        # currently keys on ``(kind, mime)`` only.
        if not section_kind:
            section_kind = "text"
        del section_kind
        return self._registry.get((kind, mime), self._fallback)

    @property
    def fallback(self) -> Chunker:
        """Expose the fallback chunker — useful for contract tests + Silver wiring."""
        return self._fallback

    def registered_keys(self) -> tuple[tuple[str, str], ...]:
        """Snapshot of registered ``(kind, mime)`` keys — for tests and diagnostics."""
        return tuple(sorted(self._registry.keys()))
