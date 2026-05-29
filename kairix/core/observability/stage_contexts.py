"""Typed StageContext shapes — ADR-026 Track A pre-work A.0c.

Every pipeline stage (fetch, bronze, extract, silver, chunk_write,
entity_buffer) consumes a different set of inputs. Today
``ConnectorPipeline._process_item`` threads those inputs as positional
+ keyword arguments to bespoke helper functions; the heterogeneous
shape obstructed the Stage abstraction audit (ADR-026 §A.0c) because
a single ``Stage.process(ctx)`` signature could not accept the union
without losing type safety.

This module defines a base :class:`StageContext` and one frozen
dataclass per stage carrying exactly the fields that stage's
implementation reads. The contexts are pure data — no behaviour, no
mutation. The Stage / StageRunner abstraction (Track A main) consumes
these contexts; Track A pre-work A.0c lands them ahead of time so the
main migration can be reviewed independently of the context
extraction.

Design notes
------------

* All contexts inherit ``StageContext(source_name, item_id)`` so the
  runner can correlate every emit with its source + item without each
  subclass having to redeclare those fields.
* All dataclasses are ``frozen=True, kw_only=True``. Frozen because
  the context represents the stage's input snapshot — mutating it
  mid-stage would defeat the point. ``kw_only=True`` avoids Python's
  "non-default after default" trap in inheritance and forces explicit
  field naming at construction sites, which makes ``_process_item``'s
  Track A rewrite readable at a glance.
* The contexts reference Protocol-surface types only
  (``RawArtefact``, ``BronzeRef``, ``ExtractedDocument``, ``Chunk``,
  ``EntitySignal``, ``SourceMetadata``, ``Sensitivity``). No coupling
  to specific connector / extractor / writer implementations — the
  Stage abstraction stays Protocol-only.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from kairix.core.protocols import (
    BronzeRef,
    Chunk,
    EntitySignal,
    ExtractedDocument,
    RawArtefact,
    Sensitivity,
    SourceMetadata,
)


@dataclass(frozen=True, kw_only=True)
class StageContext:
    """Base context for every pipeline stage.

    Carries the two correlation identifiers every emit needs:
    ``source_name`` (which connector produced the item) and ``item_id``
    (the per-source unique identifier from the ``ChangeEvent``).
    Subclasses add the stage-specific input fields.
    """

    source_name: str
    item_id: str


@dataclass(frozen=True, kw_only=True)
class FetchContext(StageContext):
    """Inputs for the fetch stage.

    The fetch stage is a side-effecting read against the source
    (HTTP, filesystem, OAuth-authenticated graph API). It needs only
    the correlation identifiers — the connector instance is held by
    the Stage subclass, not threaded through the context.
    """


@dataclass(frozen=True, kw_only=True)
class BronzeContext(StageContext):
    """Inputs for the bronze stage.

    The bronze stage persists the raw artefact under the bronze tree
    and writes the ``bronze_records`` row. It consumes the
    :class:`RawArtefact` returned by the fetch stage.
    """

    raw_artefact: RawArtefact


@dataclass(frozen=True, kw_only=True)
class ExtractContext(StageContext):
    """Inputs for the extract stage.

    The extract stage routes the raw artefact through an
    :class:`~kairix.core.protocols.Extractor` Protocol implementation
    keyed on mime type. The :class:`RawArtefact` carries both the
    bytes (``raw``) and the mime hint (``mime``).
    """

    raw_artefact: RawArtefact


@dataclass(frozen=True, kw_only=True)
class SilverContext(StageContext):
    """Inputs for the silver stage.

    Silver is the chunk + entity-signal extractor — the broadest
    context because it consumes both the bronze reference AND the
    extracted document, plus per-source envelope metadata (ADR-021)
    and the extractor-identity fields (GH #336 / ADR-024 Bundle B).
    """

    bronze_ref: BronzeRef
    extracted_document: ExtractedDocument
    source_uri: str | None
    source_modified_at: str
    sensitivity: Sensitivity
    connector_metadata: SourceMetadata
    extractor_metadata: SourceMetadata
    extractor_name: str | None
    extractor_version: str | None
    extraction_status: str


@dataclass(frozen=True, kw_only=True)
class ChunkWriteContext(StageContext):
    """Inputs for the chunk-write stage.

    The chunk-write stage persists Silver's chunk output through the
    :class:`~kairix.core.protocols.ChunkWriter` Protocol. The
    transaction boundary is owned by the runner (per-batch commit /
    rollback) — the stage itself only declares the write intent.
    """

    chunks: Sequence[Chunk]


@dataclass(frozen=True, kw_only=True)
class EntityBufferContext(StageContext):
    """Inputs for the entity-buffer stage.

    The entity-buffer stage hands Silver's entity signals to the
    :class:`~kairix.core.protocols.EntityGraphSink` (renamed
    ``stage()`` → ``buffer()`` in ADR-026 A.0a pre-work). Signals
    accumulate in the SQLite staging table; an async drain pushes to
    Neo4j on a separate worker tick.
    """

    entity_signals: Sequence[EntitySignal]


__all__ = [
    "BronzeContext",
    "ChunkWriteContext",
    "EntityBufferContext",
    "ExtractContext",
    "FetchContext",
    "SilverContext",
    "StageContext",
]
