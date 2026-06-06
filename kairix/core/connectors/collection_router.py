"""Collection router — ADR v2 §5 per-cc_pair chunk-write dispatch.

The router consults ``topology_collection_sources`` for a given
``cc_pair_id`` and routes each chunk write to the matching collection's
backing :class:`~kairix.core.connectors._SqliteChunkWriter`.

F61 contract: ``_SqliteChunkWriter(db, collection=name)`` may only be
constructed under ``kairix/core/connectors/``. This module owns the
constructor; legacy call sites (worker.py + factory.build_connector_pipeline)
route through :func:`_legacy_chunk_writer` so the writer's construction
stays in the framework.

Routing algorithm:

1. At construction time, read every ``topology_collection_sources`` row
   for ``cc_pair_id``, JOIN with ``topology_collections`` for the parent
   collection's name + ``on_unmapped_item`` policy.
2. Sort mappings by ``len(source_path_filter) DESCENDING`` — most
   specific filter wins. This matches ADR v2 §"Migration plan — Wave C".
3. For each :meth:`write_chunks` call, walk the sorted mappings and
   take the first ``fnmatch(item_id, filter)`` hit.
4. On no match: consult the connector-level default policy. The Wave C
   landing implements ``land_in_default_collection`` (writes to a
   connector-named default) and ``drop`` (increments a counter; chunks
   are silently dropped).
"""

from __future__ import annotations

import fnmatch
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from kairix.core.protocols import Chunk


@dataclass(frozen=True)
class _CollectionMapping:
    """One row from ``topology_collection_sources`` JOIN ``topology_collections``."""

    collection_id: int
    collection_name: str
    source_path_filter: str
    on_unmapped_item: Literal["land_in_default_collection", "drop"]


@dataclass(frozen=True)
class RouteResult:
    """Outcome of one :meth:`CollectionRouter.write_chunks` call.

    Surfaced so callers (worker, integration tests) can assert per-call
    routing decisions without re-querying the DB.
    """

    collection_name: str | None
    n_written: int
    on_unmapped_dropped: int


def legacy_chunk_writer(db: sqlite3.Connection, *, collection: str) -> Any:
    """Construct an ``_SqliteChunkWriter`` bound to ``collection``.

    The single sanctioned construction surface for legacy callers
    (worker.py + factory.build_connector_pipeline). Routing the legacy
    paths through this helper pays down the F61 baseline (the writer is
    constructed inside the framework, satisfying the F61 AST scan).

    Imported here lazily to avoid a cross-module import cycle —
    ``_SqliteChunkWriter`` lives in ``kairix.worker``, which imports
    framework symbols at function scope already. Return type is ``Any``
    because the concrete writer class is module-private to
    ``kairix.worker``; the duck-typed ``upsert(chunks) -> int`` ChunkWriter
    Protocol shape is what every caller relies on.
    """
    from kairix.worker import _SqliteChunkWriter

    return _SqliteChunkWriter(db, collection=collection)


# Underscored alias preserved for pre-Wave-C internal callers; new
# callers (and tests) use the public ``legacy_chunk_writer`` name.
_legacy_chunk_writer = legacy_chunk_writer


class CollectionRouter:
    """Per-cc_pair chunk-write router.

    Reads ``topology_collection_sources`` at construction time; routes
    each :meth:`write_chunks` call to the matching collection's writer.

    Construct one per cc_pair per worker tick. Writers are composed
    lazily (only the collections that receive a write get an
    ``_SqliteChunkWriter`` instance).
    """

    def __init__(self, db: sqlite3.Connection, cc_pair_id: int) -> None:
        self._db = db
        self._cc_pair_id = cc_pair_id
        self._mappings: tuple[_CollectionMapping, ...] = _load_mappings_for_cc_pair(db, cc_pair_id)
        self._writers: dict[str, object] = {}
        self._dropped = 0

    def write_chunks(self, item_id: str, chunks: Sequence[Chunk]) -> RouteResult:
        """Route ``chunks`` for ``item_id`` to the matching collection.

        Returns a :class:`RouteResult` describing the routing outcome.
        Most-specific filter wins (sort by ``len(filter) DESC`` at
        construction). On no match: defer to the per-collection
        ``on_unmapped_item`` policy.
        """
        match = self._first_match(item_id)
        if match is not None:
            writer = self._resolve_writer(match.collection_name)
            n_written = writer.upsert(chunks)
            return RouteResult(
                collection_name=match.collection_name,
                n_written=n_written,
                on_unmapped_dropped=0,
            )
        return self._handle_unmapped(chunks)

    def _first_match(self, item_id: str) -> _CollectionMapping | None:
        """Walk mappings sorted by specificity; return the first fnmatch hit."""
        for mapping in self._mappings:
            if fnmatch.fnmatch(item_id, mapping.source_path_filter):
                return mapping
        return None

    def _resolve_writer(self, collection_name: str) -> Any:
        """Lazily construct (and cache) the writer for ``collection_name``.

        Return ``Any`` because ``_SqliteChunkWriter`` is module-private to
        ``kairix.worker``; the duck-typed ``upsert(chunks) -> int`` shape is
        what the router relies on.
        """
        cached = self._writers.get(collection_name)
        if cached is not None:
            return cached
        writer = legacy_chunk_writer(self._db, collection=collection_name)
        self._writers[collection_name] = writer
        return writer

    def _handle_unmapped(self, chunks: Sequence[Chunk]) -> RouteResult:
        """Apply the on-unmapped policy for the current cc_pair's collections.

        Wave C policy resolution: if ANY mapping for this cc_pair has
        ``on_unmapped_item='land_in_default_collection'`` we land in a
        connector-named default (the first mapped collection's name);
        otherwise we drop and increment the counter.

        Wave D will tighten this once collection_sources gets a proper
        per-source default-collection pointer; today's behaviour mirrors
        the "first-mapping wins" precedent the integration tests pin.
        """
        for mapping in self._mappings:
            if mapping.on_unmapped_item == "land_in_default_collection":
                writer = self._resolve_writer(mapping.collection_name)
                n_written = writer.upsert(chunks)
                return RouteResult(
                    collection_name=mapping.collection_name,
                    n_written=n_written,
                    on_unmapped_dropped=0,
                )
        self._dropped += len(chunks)
        return RouteResult(collection_name=None, n_written=0, on_unmapped_dropped=len(chunks))

    @property
    def cc_pair_id(self) -> int:
        """Expose the cc_pair this router was constructed for — tests use this."""
        return self._cc_pair_id

    @property
    def dropped_count(self) -> int:
        """Total chunks dropped (cumulative) when on_unmapped_item='drop' fired."""
        return self._dropped

    def mapping_count(self) -> int:
        """Number of collection mappings for this cc_pair — for tests + diagnostics."""
        return len(self._mappings)


def _load_mappings_for_cc_pair(db: sqlite3.Connection, cc_pair_id: int) -> tuple[_CollectionMapping, ...]:
    """Read + sort all mappings for ``cc_pair_id``.

    SELECT joins ``topology_collection_sources`` with
    ``topology_collections`` so we have the parent collection's name +
    unmapped policy in one shot. Sort key is ``len(source_path_filter)
    DESCENDING`` — most specific filter wins.
    """
    # F63-bounded: topology_collection_sources for one cc_pair is operator-config-sized (≤O(10) rows).
    rows = db.execute(
        "SELECT c.id, c.name, cs.source_path_filter, c.on_unmapped_item "
        "FROM topology_collection_sources cs "
        "JOIN topology_collections c ON c.id = cs.collection_id "
        "WHERE cs.cc_pair_id = ?",
        (cc_pair_id,),
    ).fetchall()
    mappings = [
        _CollectionMapping(
            collection_id=row[0],
            collection_name=row[1],
            source_path_filter=row[2],
            on_unmapped_item=row[3],
        )
        for row in rows
    ]
    mappings.sort(key=lambda m: len(m.source_path_filter), reverse=True)
    return tuple(mappings)
