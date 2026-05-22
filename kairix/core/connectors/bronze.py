"""Bronze persistence — raw-bytes-as-fetched storage for the connector pipeline.

Bronze is the first storage tier in the Bronze/Silver/Gold model
locked by ``docs/architecture/connector-ingestion-architecture.md`` §2
+ §3 and kairix-pro-platform ADR-018 (storage tiering). Implementations
preserve the exact bytes the connector fetched - no chunking, no
extraction, no transformation - so re-extraction with a newer
:class:`~kairix.core.protocols.Extractor` version can rebuild the
Silver tier without re-fetching from the source system.

Storage model is filesystem-with-pointer: the bytes go to
``<bronze_root>/<source>/<hash[:2]>/<hash>``; a SQLite ``bronze_records``
row carries ``(source_name, item_id, raw_path, mime, fetched_at)`` for
replayability. The :class:`BronzeStore` Protocol on
:mod:`kairix.core.protocols` is the public seam consumers depend on;
this module ships the production implementation.

Atomicity: the caller's per-batch transaction owns the commit; this
store issues SQL but never calls ``commit()`` or ``rollback()``. The
filesystem write happens before the SQL row insert so a crash between
fsync and commit leaves an unreferenced blob (harmless garbage that
can be GC'd by a sweeper); a crash before fsync rolls back the row
write on the next transaction so nothing references a missing blob.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from kairix.core.protocols import BronzeRef, MimeType


def _content_hash(raw: bytes) -> str:
    """SHA-256 hash of raw bytes, hex-encoded."""
    return hashlib.sha256(raw).hexdigest()


class FilesystemBronzeStore:
    """Production :class:`~kairix.core.protocols.BronzeStore` implementation.

    Persists raw bytes to ``<bronze_root>/<source>/<hash[:2]>/<hash>``
    and records a pointer row in SQLite. The caller's per-batch
    transaction owns the commit; the store never commits on its own.
    """

    def __init__(self, db: sqlite3.Connection, bronze_root: Path | str) -> None:
        self._db = db
        self._bronze_root = Path(bronze_root)

    def write(self, source_name: str, item_id: str, raw: bytes, mime: MimeType) -> BronzeRef:
        """Persist bytes to disk and upsert a SQLite pointer row.

        Idempotent on ``(source_name, item_id)`` — repeated writes for
        the same key overwrite the existing pointer. Does NOT commit;
        the caller's per-batch transaction owns the commit.
        """
        digest = _content_hash(raw)
        rel_path = f"{source_name}/{digest[:2]}/{digest}"
        abs_path = self._bronze_root / rel_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temp path then rename, so a partial write never
        # leaves a half-blob at the final path.
        tmp_path = abs_path.with_suffix(abs_path.suffix + ".tmp")
        tmp_path.write_bytes(raw)
        tmp_path.replace(abs_path)
        fetched_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        self._db.execute(
            "INSERT OR REPLACE INTO bronze_records "
            "(source_name, item_id, raw_path, mime, fetched_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (source_name, item_id, rel_path, mime, fetched_at),
        )
        return BronzeRef(
            source_name=source_name,
            item_id=item_id,
            raw_path=rel_path,
            mime=mime,
            fetched_at=fetched_at,
        )

    def read(self, ref: BronzeRef) -> tuple[bytes, MimeType]:
        """Read the bytes referenced by ``ref`` plus the recorded mime hint.

        Raises :class:`FileNotFoundError` if the blob is missing on
        disk (caller's responsibility to surface).
        """
        abs_path = self._bronze_root / ref.raw_path
        return abs_path.read_bytes(), ref.mime

    def replay(self, source_name: str, since: datetime | None = None) -> Iterator[BronzeRef]:
        """Yield :class:`BronzeRef` rows for ``source_name``, oldest first.

        ``since`` restricts the stream to records with
        ``fetched_at >= since.isoformat()``. Used by re-extraction
        workflows after an :class:`Extractor` version bumps.
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
