"""usearch-backed ANN vector index for kairix.

usearch HNSW ANN index for
sub-10ms vector search at 50K+ vectors. Memory-mapped persistence
means near-zero RAM for read workloads.

The index file lives alongside index.sqlite:
  ~/.cache/kairix/vectors.usearch  (HNSW index)
  ~/.cache/kairix/vectors.meta.json (key → hash_seq mapping)
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, TypedDict

import numpy as np

from kairix.core.db import EMBED_VECTOR_DIMS, open_db

logger = logging.getLogger(__name__)

# Default dimensions — reads KAIRIX_EMBED_DIMS env var (default 1536)
DIMS = EMBED_VECTOR_DIMS

# Default number of vector results to retrieve before fusion
VECTOR_DEFAULT_K: int = 20


class VecResult(TypedDict):
    """Single vector search result."""

    hash_seq: str
    distance: float
    path: str
    collection: str
    title: str
    snippet: str


class VectorIndex:
    """usearch-backed ANN index with collection-scoped search."""

    def __init__(
        self,
        index_path: Path,
        meta_path: Path,
        db_path: Path,
        ndim: int = DIMS,
    ) -> None:
        self._index_path = Path(index_path)
        self._meta_path = Path(meta_path)
        self._db_path = Path(db_path)
        self._ndim = ndim
        self._index: Any = None
        self._key_to_hash_seq: dict[int, str] = {}
        self._next_key: int = 0

    def __len__(self) -> int:
        if self._index is None:
            return 0
        return len(self._index)

    def load(self) -> int:
        """Load existing usearch index + metadata from disk."""
        from usearch.index import Index

        if not self._index_path.exists():
            return 0
        self._index = Index.restore(str(self._index_path), view=True)
        if self._meta_path.exists():
            meta = json.loads(self._meta_path.read_text(encoding="utf-8"))
            self._key_to_hash_seq = {int(k): v for k, v in meta["keys"].items()}
            self._next_key = meta.get("next_key", max(self._key_to_hash_seq.keys(), default=-1) + 1)
        return len(self._index)

    def build_from_vectors(self, hash_seqs: list[str], vectors: np.ndarray) -> int:
        """Build a new index from provided vectors. Saves to disk."""
        from usearch.index import Index

        n = len(hash_seqs)
        if n == 0:
            return 0
        self._index = Index(ndim=self._ndim, metric="cos", dtype="f32")
        keys = np.arange(n, dtype=np.int64)
        self._index.add(keys, vectors)
        self._key_to_hash_seq = {int(k): hs for k, hs in zip(keys, hash_seqs, strict=True)}
        self._next_key = n
        self._save()
        return n

    def search(
        self,
        query_vec: np.ndarray,
        k: int = 10,
        collections: list[str] | None = None,
    ) -> list[dict]:
        """ANN search with optional collection filtering.

        Returns list of VecResult-compatible dicts sorted by distance.
        """
        if self._index is None or len(self._index) == 0:
            return []

        # Over-fetch when filtering by collection
        fetch_k = min(k * 4 if collections else k, len(self._index))
        matches = self._index.search(query_vec.astype(np.float32), fetch_k)

        # Resolve metadata from SQLite
        results = []
        try:
            db = open_db(Path(self._db_path))
            db.row_factory = sqlite3.Row
            for key, distance in zip(matches.keys, matches.distances, strict=True):
                hash_seq = self._key_to_hash_seq.get(int(key))
                if hash_seq is None:
                    continue
                content_hash = hash_seq.rsplit("_", 1)[0]
                row = db.execute(
                    "SELECT d.path, d.collection, d.title, COALESCE(c.doc, '') AS snippet "
                    "FROM documents d LEFT JOIN content c ON d.hash = c.hash "
                    "WHERE d.hash = ? AND d.active = 1 LIMIT 1",
                    (content_hash,),
                ).fetchone()
                if row is None:
                    continue
                if collections and row["collection"] not in collections:
                    continue
                results.append(
                    {
                        "hash_seq": hash_seq,
                        "distance": float(distance),
                        "path": row["path"],
                        "collection": row["collection"],
                        "title": row["title"],
                        "snippet": row["snippet"][:300] if row["snippet"] else "",
                    }
                )
                if len(results) >= k:
                    break
            db.close()
        except (sqlite3.Error, OSError) as e:
            logger.warning("vec_index: metadata lookup failed — %s", e)

        return results

    def _ensure_mutable(self) -> None:
        """Ensure the index is mutable (not a read-only mmap view).

        usearch Index.restore(view=True) creates an immutable memory-mapped
        index. To add vectors we need a mutable copy. This rebuilds the
        index from the existing vectors when needed.
        """
        from usearch.index import Index

        if self._index is None:
            self._index = Index(ndim=self._ndim, metric="cos", dtype="f32")
            return

        # Check if the index is immutable by attempting a dummy operation
        try:
            # If this succeeds, the index is already mutable
            test_key = np.array([self._next_key], dtype=np.int64)
            test_vec = np.zeros((1, self._ndim), dtype=np.float32)
            self._index.add(test_key, test_vec)
            self._index.remove(test_key)
        except Exception:
            # Index is immutable — rebuild as mutable
            logger.info("vec_index: converting immutable index to mutable (%d vectors)", len(self._index))
            old_keys = np.array(list(self._key_to_hash_seq.keys()), dtype=np.int64)
            old_vecs = np.array([self._index[k] for k in old_keys], dtype=np.float32)
            self._index = Index(ndim=self._ndim, metric="cos", dtype="f32")
            if len(old_keys) > 0:
                self._index.add(old_keys, old_vecs)

    def add_vectors(self, hash_seqs: list[str], vectors: list[list[float]]) -> int:
        """Add new vectors incrementally. Saves index after adding."""
        if not hash_seqs:
            return 0
        self._ensure_mutable()

        arr = np.array(vectors, dtype=np.float32)
        keys = np.arange(self._next_key, self._next_key + len(hash_seqs), dtype=np.int64)
        self._index.add(keys, arr)
        for k, hs in zip(keys, hash_seqs, strict=True):
            self._key_to_hash_seq[int(k)] = hs
        self._next_key += len(hash_seqs)
        self._save()
        return len(hash_seqs)

    def _save(self) -> None:
        """Save index and metadata to disk."""
        if self._index is None:
            return
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        self._index.save(str(self._index_path))
        meta = {
            "keys": {str(k): v for k, v in self._key_to_hash_seq.items()},
            "next_key": self._next_key,
            "ndim": self._ndim,
        }
        self._meta_path.write_text(json.dumps(meta), encoding="utf-8")
