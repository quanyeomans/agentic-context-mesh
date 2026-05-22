"""kairix connector plugin layer.

Per-source connector plugins live under ``kairix/connectors/<name>/``
and register via the ``kairix.connectors`` Python entry-point group in
their distribution's ``pyproject.toml`` (see the empty
``[project.entry-points."kairix.connectors"]`` table — Wave 2 adds the
first entry for ``obsidian``). Core code never imports a concrete
connector — only the ``SourceConnector`` Protocol re-exported here.

The three-layer split this package locks in mirrors the provider-plugin
shape (``kairix/providers/``):

- ``kairix/core/connectors/`` — orchestration (Bronze write, Silver
  process, cursor advance, dead-letter, all in one SQLite transaction);
  talks to plugins via the ``SourceConnector`` Protocol only.
- ``kairix/connectors/<name>/`` — per-source plugins (obsidian,
  sharepoint, m365_*, dex_crm, ...); independently shippable.
- ``kairix/extractors/<name>/`` — per-format plugins (markitdown, OCR,
  pdf_fallback, ...); independently shippable.

Fitness functions F34/F35/F36/F37/F38 lock the layer boundaries
mechanically; F41 requires ``py.typed`` on every plugin subdirectory;
F42 enforces frozen-dataclass returns at the boundary.

See ``docs/architecture/connector-ingestion-architecture.md`` (§2 + §8)
for the full architectural decision record and the
kairix-pro-platform ADR-019 entry-points discovery pattern this package
implements.
"""

from __future__ import annotations

from kairix.connectors._base import (
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
