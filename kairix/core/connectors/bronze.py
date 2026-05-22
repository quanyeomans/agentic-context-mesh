"""Bronze persistence — raw-bytes-as-fetched storage for the connector pipeline.

Bronze is the first storage tier in the Bronze/Silver/Gold model
locked by ``docs/architecture/connector-ingestion-architecture.md`` §2
+ §3 and kairix-pro-platform ADR-018 (storage tiering). Implementations
preserve the exact bytes the connector fetched - no chunking, no
extraction, no transformation - so re-extraction with a newer
:class:`~kairix.core.protocols.Extractor` version can rebuild the
Silver tier without re-fetching from the source system.

Storage model is filesystem-with-pointer: the bytes go to
``.kairix/bronze/<source>/<hash>``; a SQLite ``bronze_records`` row
carries ``(source_name, item_id, raw_path, mime, fetched_at)`` for
replayability. The :class:`BronzeStore` Protocol on
:mod:`kairix.core.protocols` is the public seam consumers depend on;
this module ships the production implementation skeleton (Wave 2
fills in the bodies).

Method bodies are intentionally single-statement
``raise NotImplementedError`` calls so the F19 unused-parameter rule
recognises them as abstract-style skeletons. Per-method intent is
captured in the comments immediately preceding each ``def`` until
Wave 2 lands the real bodies.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

from kairix.core.protocols import BronzeRef, MimeType


class FilesystemBronzeStore:
    """Production :class:`~kairix.core.protocols.BronzeStore` implementation.

    Wave 1 ships the seam-and-shape only; method bodies raise
    :class:`NotImplementedError`. Wave 2 (IM-1 / IM-2) lands the real
    write / read / replay logic - atomic fsync-then-commit ordering
    inside the per-batch SQLite transaction so Bronze write and cursor
    advance commit together or roll back together.
    """

    # write(source_name, item_id, raw, mime) -> BronzeRef
    # Wave 2: persist bytes to ``paths.bronze_root() / source_name / hash``
    # then UPSERT the SQLite ``bronze_records`` row. Idempotent on
    # ``(source_name, item_id)``. The returned :class:`BronzeRef`
    # carries the on-disk path the SilverProcessor and replay paths
    # read back from.
    def write(self, source_name: str, item_id: str, raw: bytes, mime: MimeType) -> BronzeRef:
        raise NotImplementedError("FilesystemBronzeStore.write - Wave 2 (SC-1 ships the seam only).")

    # read(ref) -> (bytes, mime)
    # Wave 2: open ``paths.bronze_root() / ref.raw_path`` and return
    # ``(bytes, ref.mime)``. Raises ``FileNotFoundError`` if the blob
    # is missing (caller's responsibility to surface).
    def read(self, ref: BronzeRef) -> tuple[bytes, MimeType]:
        raise NotImplementedError("FilesystemBronzeStore.read - Wave 2 (SC-1 ships the seam only).")

    # replay(source_name, since=None) -> Iterator[BronzeRef]
    # Wave 2: SELECT from ``bronze_records`` filtered by source +
    # fetched_at, yield :class:`BronzeRef` rows lazily. Used by
    # re-extraction workflows after an
    # :class:`~kairix.core.protocols.Extractor` version bumps.
    def replay(self, source_name: str, since: datetime | None = None) -> Iterator[BronzeRef]:
        raise NotImplementedError("FilesystemBronzeStore.replay - Wave 2 (SC-1 ships the seam only).")
