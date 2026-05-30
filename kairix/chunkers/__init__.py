"""Per-type Chunker plugin package — ADR-028 Wave G.1.

Each module under this package implements one :class:`~kairix.core.protocols.Chunker`
for a specific ``(kind, mime)`` slice (Slack threads, calendar events, …).
Plugins are wired into the runtime via the ``[project.entry-points."kairix.chunkers"]``
table in ``pyproject.toml`` and registered against
:class:`~kairix.core.connectors.chunker_registry.ChunkerRegistry` at worker boot.

F55 contract: every plugin declares ``version: str`` on the class AND
threads that string through to every emitted :class:`~kairix.core.protocols.Chunk`
via ``chunker_version=self.version`` so the maintenance-tick re-chunk sweep
can filter the affected corpus when a chunker bumps its version.

See ``docs/architecture/ADR-028-per-type-chunking-and-evaluation.md`` for
the per-type rationale (failure modes of flat splitting per source type)
and the registry-population table.
"""

from __future__ import annotations
