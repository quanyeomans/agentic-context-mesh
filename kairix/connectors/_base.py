"""SourceConnector Protocol + value objects re-exported for plugin authors.

Plugin authors import from ``kairix.connectors`` rather than reaching
into ``kairix.core`` — the indirection keeps the published wheel's
public surface stable even as the canonical Protocol definition moves
between modules.

Production plugins ship under ``kairix/connectors/<name>/`` and expose
a ``make_connector`` factory function registered via the
``kairix.connectors`` entry-point group (see ``pyproject.toml`` +
kairix-pro-platform ADR-019).

The canonical definitions live in ``kairix.core.protocols``; this
module is the published-wheel-facing import surface so plugin code
doesn't reach into ``core``.
"""

from __future__ import annotations

from kairix.core.protocols import (
    ChangeEvent,
    Cursor,
    MimeType,
    RawArtefact,
    Sensitivity,
    SourceConnector,
)

__all__ = [
    "ChangeEvent",
    "Cursor",
    "MimeType",
    "RawArtefact",
    "Sensitivity",
    "SourceConnector",
]
