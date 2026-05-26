"""StreamingBronzeStore — metadata-only bronze persistence (Phase 1 of #27).

Satisfies the :class:`~kairix.core.protocols.BronzeStore` Protocol
without persisting raw bytes to disk. The bronze_records row stores
``(source_name, item_id, mime, fetched_at)`` but ``raw_path`` is an
empty sentinel — Phase 3 of the streaming-bronze rollout makes
``BronzeRef.raw_path`` officially nullable; for Phase 1 we use the
empty string as the sentinel because the existing schema requires
a non-NULL value.

See ``docs/architecture/streaming-bronze-plan.md`` for the full
8-phase plan. Phase 1 ships the class behind opt-in wiring; Phase 4
adds the ``bronze_mode: streaming`` config field that lets operators
select this implementation per connector.

Why streaming bronze:

  The v2026.5.27a2 SharePoint dogfood produced 112 GB of persistent
  bronze for 8,783 items (avg 13 MB/item). At production-corpus scale
  this is unsustainable on commodity-disk deployments. Bronze.read
  is only ever called by the re-extract recovery path (Bug D); the
  main pipeline uses the in-memory bytes from connector.fetch directly.
  So the persistent-blob layer exists solely as a cache for an
  episodic operation. Streaming bronze replaces that cache with
  re-fetch-from-source via connector.fetch(item_id), trading
  re-extract latency for ~6000x less disk.

Atomicity matches FilesystemBronzeStore: the caller's per-batch
transaction owns the commit; this store issues SQL but never calls
``commit()`` or ``rollback()``.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import datetime, timezone

from kairix.core.protocols import BronzeRef, MimeType


class BronzeNotPersistedError(RuntimeError):
    """Raised when ``StreamingBronzeStore.read(ref)`` is called.

    Streaming bronze does not retain raw bytes. The re-extract path
    must route through ``connector.fetch(item_id)`` instead of
    ``bronze.read(ref)`` to recover the bytes from source. Phase 3 +
    Phase 5 of the streaming-bronze rollout update Bug D
    (``run_reextract_dead_letter``) to do this routing automatically;
    until then any caller that lands here is using the wrong API for
    streaming-mode deployments.
    """


# Sentinel value written into ``bronze_records.raw_path`` for streaming
# rows. Phase 3 makes the column properly nullable; for Phase 1 the
# empty string distinguishes streaming rows from FilesystemBronzeStore
# rows (which always contain a path like "source/ab/abc123..."). Public
# so callers (Bug D re-extract path in Phase 5) can compare against the
# sentinel without reaching into module-private state.
STREAMING_RAW_PATH = ""


class StreamingBronzeStore:
    """Metadata-only :class:`~kairix.core.protocols.BronzeStore`.

    ``write`` records the (source_name, item_id, mime, fetched_at)
    tuple in ``bronze_records`` without touching disk. ``read`` raises
    :class:`BronzeNotPersistedError` because there are no bytes to
    return — callers route through ``connector.fetch(item_id)``
    instead. ``replay`` yields the metadata rows so re-extract
    workflows can walk the source's history without on-disk blobs.

    The Protocol-compliance discipline is identical to
    :class:`FilesystemBronzeStore`: caller-owned transaction, no
    self-committing, returns a :class:`BronzeRef` from write so the
    pipeline carries the same value-object across both stores.
    """

    def __init__(self, db: sqlite3.Connection) -> None:
        """Construct against an open SQLite connection.

        Unlike :class:`FilesystemBronzeStore`, no ``bronze_root`` is
        needed — streaming bronze writes no files. The connection
        carries the bronze_records schema (created by
        ``kairix.core.db.schema.create_schema``).
        """
        self._db = db

    def write(self, source_name: str, item_id: str, _raw: bytes, mime: MimeType) -> BronzeRef:
        """Record the fetch metadata; discard the raw bytes.

        ``_raw`` is accepted to satisfy the BronzeStore Protocol but is
        NOT persisted (F19-prefix: underscore signals the Protocol
        position is required but this impl ignores it) — the caller
        has already handed the bytes to the extractor via the in-memory
        pipeline. Idempotent on ``(source_name, item_id)`` — repeated
        writes overwrite the metadata row.

        Does NOT commit; caller's per-batch transaction owns the commit.
        """
        fetched_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        self._db.execute(
            "INSERT OR REPLACE INTO bronze_records "
            "(source_name, item_id, raw_path, mime, fetched_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (source_name, item_id, STREAMING_RAW_PATH, mime, fetched_at),
        )
        return BronzeRef(
            source_name=source_name,
            item_id=item_id,
            raw_path=STREAMING_RAW_PATH,
            mime=mime,
            fetched_at=fetched_at,
        )

    def read(self, ref: BronzeRef) -> tuple[bytes, MimeType]:
        """Refuse the read — streaming bronze does not retain bytes.

        Raises :class:`BronzeNotPersistedError` with an operator-actionable
        fix pointer. The re-extract path (Bug D) handles this by routing
        through ``connector.fetch(ref.item_id)`` instead.
        """
        raise BronzeNotPersistedError(
            f"streaming bronze does not retain raw bytes for "
            f"({ref.source_name}, {ref.item_id}). "
            f"fix: route through ``connector.fetch(item_id)`` to re-fetch "
            f"from source instead of ``bronze.read(ref)``. "
            f"next: see docs/architecture/streaming-bronze-plan.md § 3 "
            f"for the re-extract flow change."
        )

    def replay(self, source_name: str, since: datetime | None = None) -> Iterator[BronzeRef]:
        """Yield every bronze_records row for ``source_name``, oldest first.

        Same shape as :class:`FilesystemBronzeStore.replay` — the yielded
        :class:`BronzeRef`s have an empty ``raw_path`` so callers know to
        re-fetch through the connector rather than read from disk.

        ``since`` restricts the stream to records with
        ``fetched_at >= since.isoformat()``.
        """
        if since is None:
            rows = self._db.execute(
                "SELECT source_name, item_id, raw_path, mime, fetched_at "
                "FROM bronze_records WHERE source_name = ? "
                "ORDER BY fetched_at ASC",
                (source_name,),
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT source_name, item_id, raw_path, mime, fetched_at "
                "FROM bronze_records WHERE source_name = ? AND fetched_at >= ? "
                "ORDER BY fetched_at ASC",
                (source_name, since.isoformat()),
            ).fetchall()
        for row in rows:
            yield BronzeRef(
                source_name=str(row[0]),
                item_id=str(row[1]),
                raw_path=str(row[2]),
                mime=str(row[3]),
                fetched_at=str(row[4]),
            )
