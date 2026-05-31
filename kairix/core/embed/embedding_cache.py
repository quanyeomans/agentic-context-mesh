"""Persistent embedding cache — SQLite-backed source-of-truth for embedded vectors.

Distinct from :mod:`kairix.transport.cache.embed_cache` (transport-layer
in-process LRU for search query embeddings). This module is the
production embed pipeline's restart-resilient persistent cache: every
vector returned by the embed provider is written here before the
in-memory vec index sees it, so a crash mid-cycle never re-burns
provider $ on the same chunk.

Schema::

    CREATE TABLE embedding_cache (
        model       TEXT NOT NULL,
        dimension   INTEGER NOT NULL,
        chunk_hash  TEXT NOT NULL,
        vector      BLOB NOT NULL,
        created_at  TEXT NOT NULL,
        PRIMARY KEY (model, dimension, chunk_hash)
    )

Vectors are stored as raw little-endian f32 bytes
(``np.asarray(vec, dtype="float32").tobytes()``). The cache is the
canonical source-of-truth even when the in-memory vec index later
quantises to f16 — we re-quantise on rebuild, not on store, so a
``--force`` rebuild after a model swap reuses the f32 record losslessly.

Cache key is ``(model, dimension, chunk_hash)``. Switching models
leaves both caches independently valid; switching dimensions on the
same model name (e.g. text-embedding-3-large at 3072d vs 1536d) keeps
the two slices distinct.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# Schema literals — extracted so the create + select + upsert sites all
# reference one source. F17 (≥3 sites; the create-table + the upsert +
# the select all carry the same column ordering, so a future column add
# is a single-edit change).
_TABLE = "embedding_cache"
_DTYPE = "float32"
_CREATE_SQL = (
    f"CREATE TABLE IF NOT EXISTS {_TABLE} ("
    "  model       TEXT NOT NULL,"
    "  dimension   INTEGER NOT NULL,"
    "  chunk_hash  TEXT NOT NULL,"
    "  vector      BLOB NOT NULL,"
    "  created_at  TEXT NOT NULL,"
    "  PRIMARY KEY (model, dimension, chunk_hash)"
    ")"
)
_UPSERT_SQL = (
    f"INSERT OR REPLACE INTO {_TABLE} (model, dimension, chunk_hash, vector, created_at) VALUES (?, ?, ?, ?, ?)"
)

# Conservative chunk size for the IN-clause used by ``get_many``. SQLite
# older builds cap parameter count at 999; we leave headroom for the two
# ``model`` / ``dimension`` placeholders attached to each batch.
_IN_CLAUSE_BATCH_SIZE = 500


def _encode_vector(vector: list[float] | np.ndarray) -> bytes:
    """Convert a vector to the canonical f32 little-endian BLOB representation."""
    arr = np.asarray(vector, dtype=_DTYPE)
    return arr.tobytes()


def _decode_vector(blob: bytes, dimension: int) -> np.ndarray:
    """Decode a f32 BLOB back to a ``np.ndarray`` of length ``dimension``.

    Validates length so a corrupt cache row surfaces as ``ValueError``
    rather than silently truncating downstream tensor ops.
    """
    arr = np.frombuffer(blob, dtype=_DTYPE)
    if arr.shape[0] != dimension:
        raise ValueError(
            f"embedding_cache: vector blob length {arr.shape[0]} != declared dimension {dimension}. "
            "fix: delete the stale cache row OR rerun --force-rebuild-cache so the next embed call repopulates it. "
            "next: inspect embedding_cache.sqlite with: "
            "sqlite3 embedding_cache.sqlite 'SELECT model, dimension, chunk_hash, length(vector) FROM embedding_cache "
            f"WHERE length(vector) != {dimension * 4} LIMIT 5'. "
            "run: kairix embed --force-rebuild-cache to drop the cache file and re-embed."
        )
    return arr


def hash_chunk_text(text: str) -> str:
    """Stable hash of a chunk's text for use as the cache key.

    SHA256 is overkill for collision-resistance at corpus scale
    (~10^6 chunks) but the cost is negligible relative to the embed
    roundtrip the cache is built to avoid. Stable across processes and
    Python versions — same text always hashes the same way.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def cache_db_path(document_root: Path) -> Path:
    """Resolve the embedding cache SQLite file under an explicit document root.

    Test seam — tests pass ``tmp_path`` so the cache file lands in a
    pytest-managed directory. Production callers use
    :func:`kairix.paths.embedding_cache_path` instead, which threads
    through the F4 paths boundary.
    """
    return Path(document_root) / ".kairix" / "cache" / "embedding_cache.sqlite"


class EmbeddingCache:
    """SQLite-backed persistent cache keyed on ``(model, dimension, chunk_hash)``.

    The cache is restart-resilient: every batch of vectors returned by
    the embed provider is upserted here before the in-memory vec index
    sees it (see :func:`kairix.core.embed.embed.run_embed`). A crash
    after the provider call → cache has the vectors → the next run finds
    them and skips the provider for that chunk.

    Instances are cheap; the underlying connection is opened lazily on
    first use and reused across method calls. Callers that need explicit
    teardown (tests) call :meth:`close`.
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._conn: sqlite3.Connection | None = None

    @property
    def path(self) -> Path:
        """Disk path the cache is bound to."""
        return self._path

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # F77-allow: separate cache DB under .kairix/cache/, single writer (embed cycle).
            conn = sqlite3.connect(str(self._path))
            conn.execute(_CREATE_SQL)
            conn.commit()
            self._conn = conn
        return self._conn

    def close(self) -> None:
        """Close the underlying SQLite connection (idempotent)."""
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    def get_many(
        self,
        model: str,
        dimension: int,
        chunk_hashes: Iterable[str],
    ) -> dict[str, np.ndarray]:
        """Return cached vectors for the supplied hashes.

        Missing hashes are simply absent from the returned dict — the
        caller treats absence as a cache miss and dispatches to the
        embed provider.
        """
        hashes = list(chunk_hashes)
        if not hashes:
            return {}

        conn = self._connection()
        results: dict[str, np.ndarray] = {}
        for start in range(0, len(hashes), _IN_CLAUSE_BATCH_SIZE):
            chunk = hashes[start : start + _IN_CLAUSE_BATCH_SIZE]
            placeholders = ",".join("?" * len(chunk))
            sql = (
                f"SELECT chunk_hash, vector FROM {_TABLE} "
                f"WHERE model = ? AND dimension = ? AND chunk_hash IN ({placeholders})"
            )
            params: list[Any] = [model, dimension, *chunk]
            # F63-bounded: caller-supplied IN-clause caps result cardinality at
            # _IN_CLAUSE_BATCH_SIZE (500); the per-call batching above is the bound.
            for row_hash, row_blob in conn.execute(sql, params).fetchall():
                results[row_hash] = _decode_vector(row_blob, dimension)
        return results

    def put_many(
        self,
        model: str,
        dimension: int,
        pairs: Iterable[tuple[str, list[float] | np.ndarray]],
    ) -> int:
        """Upsert a batch of ``(chunk_hash, vector)`` pairs in one transaction.

        Returns the number of rows written. Single transaction matters:
        the production crash mode the cache exists to fix is a partial
        write between provider response and vec-index add, so the cache
        write MUST be all-or-nothing per batch.
        """
        rows: list[tuple[str, int, str, bytes, str]] = []
        created_at = datetime.now(timezone.utc).isoformat()
        for chunk_hash, vector in pairs:
            rows.append((model, dimension, chunk_hash, _encode_vector(vector), created_at))
        if not rows:
            return 0
        conn = self._connection()
        with conn:
            conn.executemany(_UPSERT_SQL, rows)
        return len(rows)

    def count(self, model: str | None = None, dimension: int | None = None) -> int:
        """Total cached rows, optionally scoped to a model / dimension pair.

        Bounded query — used by status reporting + the integration test
        that asserts cache writes happened.
        """
        conn = self._connection()
        if model is None and dimension is None:
            row = conn.execute(f"SELECT COUNT(*) FROM {_TABLE}").fetchone()
        elif model is not None and dimension is not None:
            row = conn.execute(
                f"SELECT COUNT(*) FROM {_TABLE} WHERE model = ? AND dimension = ?",
                (model, dimension),
            ).fetchone()
        else:
            raise ValueError(
                "count() requires both model and dimension or neither. "
                "fix: pass both filters together (e.g. count(model='text-embedding-3-large', dimension=1536)) "
                "or omit both for a global count. "
                "next: see kairix/core/embed/embedding_cache.py docstring for cache key semantics. "
                "run: pytest tests/unit/test_embedding_cache.py -k count"
            )
        return int(row[0])

    def clear(self) -> None:
        """Drop every row (used by ``--force-rebuild-cache`` and tests)."""
        conn = self._connection()
        with conn:
            conn.execute(f"DELETE FROM {_TABLE}")
