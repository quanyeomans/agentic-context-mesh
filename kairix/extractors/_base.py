"""Re-exports the Extractor Protocol and its value objects so plugin authors
import from ``kairix.extractors`` rather than reaching into core.

Production plugins ship under ``kairix/extractors/<name>/`` and expose a
``make_extractor`` factory function registered via the
``kairix.extractors`` entry-point group (see ``pyproject.toml``).

The canonical definitions live in ``kairix.core.protocols``; this module
is the published-wheel-facing import surface so plugin code doesn't reach
into ``core``.
"""

from __future__ import annotations

from kairix.core.protocols import (
    DocMetadata,
    ExtractedDocument,
    Extractor,
    Image,
    MimeType,
    Page,
)

__all__ = [
    "DocMetadata",
    "ExtractedDocument",
    "Extractor",
    "Image",
    "MimeType",
    "Page",
]
