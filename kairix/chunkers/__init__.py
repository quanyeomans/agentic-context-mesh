"""Per-type Chunker plugin package — ADR-028 Wave G.1.

Each module under this package implements one :class:`~kairix.core.protocols.Chunker`
for a specific ``(kind, mime)`` slice. Plugins are wired into the runtime
via the ``[project.entry-points."kairix.chunkers"]`` table in
``pyproject.toml`` and registered against
:class:`~kairix.core.connectors.chunker_registry.ChunkerRegistry` at
worker boot. Core code never imports a concrete chunker — only the
Protocol and the dispatch table.

ADR-028 (`docs/architecture/ADR-028-per-type-chunking-and-evaluation.md`)
splits chunking by document type so each format chunks on its natural
unit instead of one uniform paragraph splitter:

* PPTX → :class:`~kairix.chunkers.slide.SlideChunker` — one slide per chunk.
* XLSX / .xls / .xlsm → :class:`~kairix.chunkers.sheet_row.SheetRowChunker`
  — one row per chunk (header row prepended) for tabular sheets; whole
  sheet as one chunk for small reference sheets (<50 rows).
* DOCX → :class:`~kairix.chunkers.docx_heading.DocxHeadingChunker` —
  split on heading hierarchy (H1/H2/H3); tables emit separate chunks.
* Slack threads → :class:`~kairix.chunkers.thread.ThreadChunker` —
  thread = primary chunk; sub-split on token cap; 5-min windows for
  non-threaded streams.
* Calendar events → :class:`~kairix.chunkers.calendar_event.CalendarEventChunker`
  — one event per chunk; RRULE in metadata (no per-occurrence expansion).

F55 contract: every plugin declares ``version: str`` on the class AND
threads that string through to every emitted :class:`~kairix.core.protocols.Chunk`
via ``chunker_version=self.version`` so the maintenance-tick re-chunk sweep
can filter the affected corpus when a chunker bumps its version.
"""

from __future__ import annotations
