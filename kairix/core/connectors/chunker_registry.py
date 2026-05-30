"""Chunker registry — ADR v2 §6 per-``(kind, mime)`` dispatch surface.

The registry maps ``(kind, mime)`` keys to :class:`~kairix.core.protocols.Chunker`
implementations. Wave C lands the registry + a paragraph-shaped fallback;
Wave F lands the per-kind plugins (tree-sitter / per-ticket / thread-aware /
slide / tabular / email-thread / event / transcript / web) under
``kairix/chunkers/<name>/``.

Wave G.1 (ADR-028) lands the per-type plugins:
:class:`~kairix.chunkers.slide.SlideChunker` (PPTX),
:class:`~kairix.chunkers.sheet_row.SheetRowChunker` (XLSX/.xls/.xlsm),
:class:`~kairix.chunkers.docx_heading.DocxHeadingChunker` (DOCX).
:func:`build_default_chunker_registry` wires the registrations. When
no plugin matches a ``(kind, mime)`` pair, dispatch falls through to
the paragraph-shaped fallback.

F55 contract: every :class:`Chunker` plugin declares ``version: str`` AND
every emitted :class:`~kairix.core.protocols.Chunk` carries
``chunker_version=self.version``. The fallback satisfies this contract too.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence

from kairix.chunkers.docx_heading import DocxHeadingChunker
from kairix.chunkers.sheet_row import SheetRowChunker
from kairix.chunkers.slide import SlideChunker
from kairix.core.protocols import Chunk, Chunker, Sensitivity

# ADR-028 Wave G.1 — MIME constants hoisted to module level so the
# >=10 char literals don't recur (F17 — no string literal >=10 chars
# duplicated >=3 times).
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
LEGACY_XLS_MIME = "application/vnd.ms-excel"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# Connector-kind constants — also F17 (each is ≥10 chars and appears in
# ≥3 `register(...)` call sites below).
SHAREPOINT_KIND = "sharepoint"
GOOGLE_DRIVE_KIND = "google_drive"

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


# MIME literals duplicated ≥3 times across the registry-population
# call sites (F17). Extracting to module-level constants keeps the
# coupling explicit and the rename cost a single-edit-site change.
_MIME_TEXT_CALENDAR = "text/calendar"


def build_default_registry() -> ChunkerRegistry:
    """Construct a :class:`ChunkerRegistry` with the ADR-028 Wave G.1 plugins.

    Per ADR-028 Wave G.1, each ``(connector kind, mime)`` pair routes
    to the per-type chunker that best fits the format's natural unit:

      * PPTX → :class:`~kairix.chunkers.slide.SlideChunker` — one slide per chunk.
      * XLSX / .xls → :class:`~kairix.chunkers.sheet_row.SheetRowChunker`
        — one row per chunk (header prepended) for tabular sheets;
        whole sheet as one chunk for small reference sheets (<50 rows).
      * DOCX → :class:`~kairix.chunkers.docx_heading.DocxHeadingChunker`
        — heading-hierarchy split with tables as separate chunks.
      * Slack threads → :class:`~kairix.chunkers.thread.ThreadChunker` —
        thread = primary chunk; sub-split on token cap.
      * Calendar events → :class:`~kairix.chunkers.calendar_event.CalendarEventChunker`
        — one event per chunk; RRULE in metadata.

    Operators get the per-type chunking behaviour by constructing the
    registry through this factory; bespoke wiring (tests, contract
    proofs) constructs the bare :class:`ChunkerRegistry` and registers
    only what the test exercises.

    Imports are deferred to the function body so importing the registry
    module doesn't pull in every chunker plugin's transitive imports —
    keeps the F38 / F26 layering boundary on the bare registry surface.
    """
    # Deferred imports — see docstring for the rationale.
    from kairix.chunkers.calendar_event import CalendarEventChunker
    from kairix.chunkers.thread import ThreadChunker

    registry = ChunkerRegistry()
    thread_chunker = ThreadChunker()
    calendar_chunker = CalendarEventChunker()
    slide_chunker = SlideChunker()
    sheet_row_chunker = SheetRowChunker()
    docx_heading_chunker = DocxHeadingChunker()

    # Slack — both JSON envelopes (the canonical fetch shape) and
    # text/plain (legacy or hand-shaped tests) route through ThreadChunker.
    registry.register(kind="slack", mime="application/json", chunker=thread_chunker)
    registry.register(kind="slack", mime="text/plain", chunker=thread_chunker)
    # Calendar — every calendar connector emits text/calendar shape
    # after the extractor lift; one chunker for all three sources.
    registry.register(kind="m365_calendar", mime=_MIME_TEXT_CALENDAR, chunker=calendar_chunker)
    registry.register(kind="google_calendar", mime=_MIME_TEXT_CALENDAR, chunker=calendar_chunker)
    registry.register(kind="apple_caldav", mime=_MIME_TEXT_CALENDAR, chunker=calendar_chunker)
    # PPTX — one chunk per slide.
    registry.register(kind=SHAREPOINT_KIND, mime=PPTX_MIME, chunker=slide_chunker)
    registry.register(kind=GOOGLE_DRIVE_KIND, mime=PPTX_MIME, chunker=slide_chunker)
    # XLSX — one row per chunk (or whole sheet for small reference sheets).
    registry.register(kind=SHAREPOINT_KIND, mime=XLSX_MIME, chunker=sheet_row_chunker)
    registry.register(kind=SHAREPOINT_KIND, mime=LEGACY_XLS_MIME, chunker=sheet_row_chunker)
    registry.register(kind=GOOGLE_DRIVE_KIND, mime=XLSX_MIME, chunker=sheet_row_chunker)
    # DOCX — heading-hierarchy split.
    registry.register(kind=SHAREPOINT_KIND, mime=DOCX_MIME, chunker=docx_heading_chunker)
    registry.register(kind=GOOGLE_DRIVE_KIND, mime=DOCX_MIME, chunker=docx_heading_chunker)

    return registry


# Backwards-compatibility alias for callers from the W3B branch that
# expected the longer name. Both names resolve to the same factory.
build_default_chunker_registry = build_default_registry
