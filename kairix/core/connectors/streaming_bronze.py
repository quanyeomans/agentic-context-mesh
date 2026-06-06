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

import hashlib
import sqlite3
from collections.abc import Iterator
from datetime import datetime, timezone

from kairix.core.protocols import BronzeRef, MimeType


def _content_hash(raw: bytes) -> str:
    """SHA-256 of the raw bytes — same shape as FilesystemBronzeStore.

    Phase 2 of streaming-bronze: both impls populate bronze_records.content_hash
    so the column has the same semantics regardless of which store wrote
    the row. Streaming-mode rows can't be re-read from disk, but the hash
    is still useful for re-fetch verification (Phase 5+) and dedupe.
    """
    return hashlib.sha256(raw).hexdigest()


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
# rows. The column stays NOT NULL at the SQLite layer (avoiding a
# table-rebuild migration); the empty string distinguishes streaming
# rows from FilesystemBronzeStore rows (which always contain a path
# like "source/ab/abc123...").
#
# Phase 3 of the streaming-bronze rollout: replay() converts the
# empty-string DB value to Python ``None`` so consumers can pattern
# on ``if ref.raw_path is None:`` rather than checking truthiness.
# The DB sentinel + the Python-visible None are equivalent — the
# conversion happens in the read path.
_STREAMING_DB_SENTINEL = ""


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

    def write(self, source_name: str, item_id: str, raw: bytes, mime: MimeType) -> BronzeRef:
        """Record the fetch metadata + content hash; discard the raw bytes.

        Phase 2: streaming bronze persists ``content_hash`` so it's
        queryable without retaining the raw blob. The hash is the only
        durable fingerprint of "what was fetched" once the bytes go away.

        The caller has already handed ``raw`` to the extractor via the
        in-memory pipeline. Idempotent on ``(source_name, item_id)`` —
        repeated writes overwrite the metadata row.

        Does NOT commit; caller's per-batch transaction owns the commit.
        """
        fetched_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        digest = _content_hash(raw)
        self._db.execute(
            "INSERT OR REPLACE INTO bronze_records "
            "(source_name, item_id, raw_path, mime, fetched_at, content_hash) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (source_name, item_id, _STREAMING_DB_SENTINEL, mime, fetched_at, digest),
        )
        return BronzeRef(
            source_name=source_name,
            item_id=item_id,
            raw_path=None,  # Python-visible signal that this is a streaming row
            mime=mime,
            fetched_at=fetched_at,
            content_hash=digest,
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
            # F63-bounded: replay() is an operator-invoked rebuild path, not a tick-loop call.
            # Caller decides whether full-replay is appropriate at the source-size in question.
            rows = self._db.execute(
                "SELECT source_name, item_id, raw_path, mime, fetched_at, content_hash "
                "FROM bronze_records WHERE source_name = ? "
                "ORDER BY fetched_at ASC",
                (source_name,),
            ).fetchall()
        else:
            # F63-bounded: same replay path, additionally narrowed by since-checkpoint.
            rows = self._db.execute(
                "SELECT source_name, item_id, raw_path, mime, fetched_at, content_hash "
                "FROM bronze_records WHERE source_name = ? AND fetched_at >= ? "
                "ORDER BY fetched_at ASC",
                (source_name, since.isoformat()),
            ).fetchall()
        for row in rows:
            db_raw_path = str(row[2])
            yield BronzeRef(
                source_name=str(row[0]),
                item_id=str(row[1]),
                # Phase 3: convert the empty-string DB sentinel to None
                # so Python consumers can pattern on ``if ref.raw_path is None:``.
                raw_path=db_raw_path if db_raw_path else None,
                mime=str(row[3]),
                fetched_at=str(row[4]),
                content_hash=str(row[5]) if row[5] is not None else None,
            )
