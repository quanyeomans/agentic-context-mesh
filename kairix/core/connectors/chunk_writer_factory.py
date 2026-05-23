"""F61-compliant home for the ``_SqliteChunkWriter`` construction site.

Wave A landed the writer in ``kairix/worker.py`` (legacy single-collection
shape). Wave C will introduce the real ``CollectionRouter`` in
``kairix/core/connectors/collection_router.py`` and pay down the
``kairix/worker.py`` baseline entry. Until then, this thin helper owns
the framework-side construction so any caller outside ``kairix/core/connectors/``
(notably the ``kairix.core.factory.build_connector_pipeline`` factory)
can stay F61-clean by importing this helper rather than constructing
``_SqliteChunkWriter`` directly.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kairix.core.connectors.pipeline import ChunkWriter


def make_sqlite_chunk_writer(db: sqlite3.Connection, *, collection: str) -> ChunkWriter:
    """Construct the canonical SQLite-backed chunk writer for ``collection``.

    Wraps ``kairix.worker._SqliteChunkWriter`` to keep the constructor
    call site inside ``kairix/core/connectors/`` per F61. Wave C swaps the
    implementation behind this helper to delegate through ``CollectionRouter``
    when ``topology_v2_runtime`` is ON.
    """
    from kairix.worker import _SqliteChunkWriter

    return _SqliteChunkWriter(db, collection=collection)
