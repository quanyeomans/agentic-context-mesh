"""SourceConnector Protocol + value objects re-exported for plugin authors.

Plugin authors import from ``kairix.connectors`` rather than reaching
into ``kairix.core`` — the indirection keeps the published wheel's
public surface stable even as the canonical Protocol definition moves
between modules.

Production plugins ship under ``kairix/connectors/<name>/`` and expose
a ``make_connector`` factory function registered via the
``kairix.connectors`` entry-point group (see ``pyproject.toml`` +
kairix-pro-platform ADR-019).

TODO(SC-1): once SC-1's additions to ``kairix/core/protocols.py``
(``SourceConnector`` + value objects) land on main, swap the
definitions below for the canonical re-exports:

    from kairix.core.protocols import (
        ChangeEvent,
        Cursor,
        MimeType,
        RawArtefact,
        Sensitivity,
        SourceConnector,
    )

The placeholders below mirror the shapes in
``docs/architecture/connector-ingestion-architecture.md`` §3 verbatim
so that downstream code (Wave 2's Obsidian plugin, contract tests,
``tests/fakes.FakeSourceConnector``) can be written against this
module today without waiting on SC-1 to land.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

#: Sensitivity tier carried on every change event, raw artefact, and
#: silver-stage chunk. Maps 1:1 to the operator-facing config string in
#: ``kairix.config.yaml`` (``connectors[].sensitivity``).
Sensitivity = Literal["public", "internal", "client-confidential", "personal"]

#: MIME type hint surfaced by connectors and consumed by the extractor
#: registry. Strings keep the boundary widely interoperable —
#: connectors can return whatever the source declares.
MimeType = str

#: Resumable cursor token. Connectors are free to choose any opaque
#: string serialisation; the orchestration layer treats it as a blob.
Cursor = str


@dataclass(frozen=True)
class ChangeEvent:
    """One change observed by a connector.

    Yielded by ``SourceConnector.list_changes``. The orchestration
    layer (``kairix.core.connectors``) advances the per-connector
    cursor only after the matching Bronze + Silver writes commit, so
    a crash mid-batch replays the same events on the next worker tick.
    """

    op: Literal["created", "modified", "deleted"]
    item_id: str
    modified_at: str  # ISO-8601 UTC
    parent_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RawArtefact:
    """Raw bytes + MIME hint for one item, exactly as fetched.

    Returned by ``SourceConnector.fetch``. Persisted to Bronze
    unchanged so the extract step can be replayed when an extractor
    version bumps (see F40).
    """

    raw: bytes
    mime: MimeType
    fetched_at: str  # ISO-8601 UTC


@runtime_checkable
class SourceConnector(Protocol):
    """One external source family.

    Implementations live under ``kairix/connectors/<name>/`` and
    register via the ``kairix.connectors`` entry-point group in
    ``pyproject.toml``. Core code never imports a concrete connector —
    only this Protocol.

    The Protocol is deliberately narrow: ``list_changes`` /
    ``fetch`` / ``source_link`` / ``sensitivity_for`` only. Bronze
    persistence and Silver processing (chunking + entity-signal
    extraction) live in shared infrastructure under
    ``kairix/core/connectors/`` so every plugin gets the same chunker
    rather than re-implementing one per source. See
    ``docs/architecture/connector-ingestion-architecture.md`` §4.

    Members:

    - ``name`` (``str``): short stable name ("obsidian" | "sharepoint" |
      "dex_crm" | "m365_email_headers" | ...). Matches the entry-point
      key under ``[project.entry-points."kairix.connectors"]``.
    - ``list_changes(cursor)``: stream change events since ``cursor``.
      Resumable; the cursor advances on batch commit (per
      orchestration in ``kairix.core.connectors``).
    - ``fetch(item_id)``: fetch raw bytes + MIME hint for one item.
    - ``source_link(item_id)``: deep-link back to the source —
      surfaced in retrieval results so a user can click straight
      through.
    - ``sensitivity_for(item_id)``: return the sensitivity tier for
      this item. Defaults to the connector's config tier; per-item
      overrides allow e.g. one folder in an Obsidian vault to ship as
      ``client-confidential`` while the rest stays ``internal``.
    """

    name: str

    def list_changes(self, cursor: Cursor | None) -> Iterator[ChangeEvent]:
        """Stream changes since ``cursor``. Resumable; cursor advances on commit."""

    def fetch(self, item_id: str) -> RawArtefact:
        """Fetch raw bytes + MIME hint for an item."""

    def source_link(self, item_id: str) -> str:
        """Deep-link back to the source — surfaced in retrieval results."""

    def sensitivity_for(self, item_id: str) -> Sensitivity:
        """Return the sensitivity tier for this item."""


__all__ = [
    "ChangeEvent",
    "Cursor",
    "MimeType",
    "RawArtefact",
    "Sensitivity",
    "SourceConnector",
]
