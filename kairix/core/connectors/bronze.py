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
import time
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
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
        fetched_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        # Phase 2: persist content_hash. We've already computed it (digest)
        # above for the on-disk path; reuse the same value.
        self._db.execute(
            "INSERT OR REPLACE INTO bronze_records "
            "(source_name, item_id, raw_path, mime, fetched_at, content_hash) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (source_name, item_id, rel_path, mime, fetched_at, digest),
        )
        return BronzeRef(
            source_name=source_name,
            item_id=item_id,
            raw_path=rel_path,
            mime=mime,
            fetched_at=fetched_at,
            content_hash=digest,
        )

    def read(self, ref: BronzeRef) -> tuple[bytes, MimeType]:
        """Read the bytes referenced by ``ref`` plus the recorded mime hint.

        Raises :class:`FileNotFoundError` if the blob is missing on
        disk (caller's responsibility to surface).
        """
        abs_path = self._bronze_root / ref.raw_path
        return abs_path.read_bytes(), ref.mime

    def reap_orphans(self, source_name: str, *, min_age_seconds: float = 0.0) -> int:
        """Delete blobs under ``<bronze_root>/<source_name>/`` that no
        ``bronze_records`` row references. Returns the count deleted.

        Closes the post-fsync-pre-commit window the module docstring
        anticipates: a blob landed on disk but the SQL row never
        committed (process crash, OOM-kill, container restart), so the
        blob is unreachable garbage that no replay can ever serve.

        ``min_age_seconds`` skips files newer than the cutoff so an
        in-flight write isn't reaped mid-fsync. The 2026-05-25 incident
        reaped 522 fully-orphaned SharePoint blobs plus 309 ``.tmp``
        leftovers totalling 36 GB; the maintenance-scheduler call site
        runs every tick so the same accumulation cannot recur.
        """
        source_dir = self._bronze_root / source_name
        if not source_dir.is_dir():
            return 0
        registered = self._registered_raw_paths(source_name)
        now = time.time()
        reaped = 0
        for prefix_dir in source_dir.iterdir():
            if prefix_dir.is_dir():
                reaped += self._reap_in_prefix(prefix_dir, source_name, registered, now, min_age_seconds)
        return reaped

    def _registered_raw_paths(self, source_name: str) -> set[str]:
        """Return the set of bronze_records.raw_path values for ``source_name``."""
        return {
            str(row[0])
            for row in self._db.execute(
                "SELECT raw_path FROM bronze_records WHERE source_name = ?",
                (source_name,),
            )
        }

    def _reap_in_prefix(
        self,
        prefix_dir: Path,
        source_name: str,
        registered: set[str],
        now: float,
        min_age_seconds: float,
    ) -> int:
        """Reap every orphan file in one prefix directory; return the count."""
        reaped = 0
        for blob in prefix_dir.iterdir():
            if self._reap_one(blob, prefix_dir.name, source_name, registered, now, min_age_seconds):
                reaped += 1
        return reaped

    @staticmethod
    def _reap_one(
        blob: Path,
        prefix_name: str,
        source_name: str,
        registered: set[str],
        now: float,
        min_age_seconds: float,
    ) -> bool:
        """Delete ``blob`` if it's an unreferenced file old enough to be safe."""
        if not blob.is_file():
            return False
        rel_path = f"{source_name}/{prefix_name}/{blob.name}"
        if rel_path in registered:
            return False
        if min_age_seconds > 0.0 and (now - blob.stat().st_mtime) < min_age_seconds:
            return False
        blob.unlink()
        return True

    def gc_aged(self, source_name: str, *, older_than_days: int) -> int:
        """Delete every ``bronze_records`` row + on-disk blob for
        ``source_name`` whose ``fetched_at`` is older than the cutoff.
        Returns the count deleted.

        TTL garbage collection — the bound on bronze growth long-term.
        After ``older_than_days`` the raw bytes are dropped; if a future
        re-extraction needs them, the connector re-fetches from source.

        Does NOT commit; caller owns the transaction (matches the
        existing ``write`` / ``read`` / ``replay`` contract on this
        store). Refuses negative TTLs with a clear actionable message
        per F21.
        """
        if older_than_days < 0:
            raise ValueError(
                f"older_than_days must be >= 0; got {older_than_days!r}. "
                "fix: pass a non-negative int; "
                "run: KAIRIX_BRONZE_TTL_DAYS=7 (default)"
            )
        cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat().replace("+00:00", "Z")
        rows = self._db.execute(
            "SELECT item_id, raw_path FROM bronze_records WHERE source_name = ? AND fetched_at < ?",
            (source_name, cutoff),
        ).fetchall()
        deleted = 0
        for item_id, raw_path in rows:
            blob = self._bronze_root / str(raw_path)
            if blob.is_file():
                blob.unlink()
            self._db.execute(
                "DELETE FROM bronze_records WHERE source_name = ? AND item_id = ?",
                (source_name, item_id),
            )
            deleted += 1
        return deleted

    def replay(self, source_name: str, since: datetime | None = None) -> Iterator[BronzeRef]:
        """Yield :class:`BronzeRef` rows for ``source_name``, oldest first.

        ``since`` restricts the stream to records with
        ``fetched_at >= since.isoformat()``. Used by re-extraction
        workflows after an :class:`Extractor` version bumps.
        """
        if since is None:
            rows = self._db.execute(
                "SELECT source_name, item_id, raw_path, mime, fetched_at, content_hash "
                "FROM bronze_records WHERE source_name = ? "
                "ORDER BY fetched_at ASC",
                (source_name,),
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT source_name, item_id, raw_path, mime, fetched_at, content_hash "
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
                content_hash=str(row[5]) if row[5] is not None else None,
            )
