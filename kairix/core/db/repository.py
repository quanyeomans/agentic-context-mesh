"""SQLite-backed DocumentRepository implementation.

Wraps direct SQLite + FTS5 queries behind the DocumentRepository protocol.
All methods return safe defaults on failure ([] or None) and never raise.
"""

from __future__ import annotations

import functools
import logging
import sqlite3
from pathlib import Path
from typing import Any

from kairix.core.db import open_db

logger = logging.getLogger(__name__)

# Bound on the per-repo chunk-date LRU. Sized larger than any reasonable
# bm25_limit + vector_limit sum so a single search's enrich call never
# evicts the prior search's batch under conc>=5 traffic — eviction under
# load drives SQLite-WAL-lock contention on the enrich path.
_CHUNK_DATES_CACHE_MAX = 256


class SQLiteDocumentRepository:
    """DocumentRepository implementation backed by SQLite + FTS5.

    Satisfies kairix.core.protocols.DocumentRepository.
    """

    def __init__(self, db_path: Path, *, opener: Any = None) -> None:
        self._db_path = db_path
        # Public DI seam — production callers omit ``opener`` and the
        # repo uses the module-level ``open_db``. Tests inject a fake
        # opener to drive open-failure / cursor-failure branches without
        # monkey-patching the repository module's ``open_db`` binding.
        self._opener = opener if opener is not None else open_db
        # Bounded LRU around the per-batch chunk-date lookup. The cache key
        # is the frozenset of paths (order-independent); the value is the
        # path -> chunk_date dict. Cache invalidates on process restart,
        # which matches the chunk_date update cycle (re-embed rewrites the
        # row).  The instance-attribute pattern (rather than decorating the
        # bound method) keeps ``self`` out of the cache key, so the LRU is
        # per-repo and ``self`` is not weakly held by ``functools``.
        self._chunk_dates_cache = functools.lru_cache(maxsize=_CHUNK_DATES_CACHE_MAX)(self._get_chunk_dates_uncached)

    def _log_fts_operational_error(self, exc: sqlite3.OperationalError) -> None:
        """Log a SQLite OperationalError with severity based on missing-table vs other.

        The documents_fts-missing case is a real production fault — the
        entire BM25 leg of hybrid retrieval is offline. Log at ERROR (not
        WARNING) so it surfaces in alert pipelines, and tell the operator
        how to fix it. Other operational errors (table locked, corrupt
        index) keep the WARNING level. See #223.
        """
        msg = str(exc)
        if "no such table" in msg.lower() and "documents_fts" in msg:
            logger.error(
                "search_fts: documents_fts is missing — BM25 leg is offline, hybrid retrieval is "
                "degraded to vector-only. Run 'kairix embed --rebuild-fts' to rebuild the index."
            )
        else:
            logger.warning("SQLiteDocumentRepository.search_fts: FTS query failed — %s", exc)

    def _row_to_search_result(self, row: sqlite3.Row) -> dict[str, Any]:
        """Map one FTS row into the result dict consumed by the search backend."""
        raw_score = float(row["bm25_score"])
        score = abs(raw_score) / (1.0 + abs(raw_score))
        # PLA-269 — the snippet is an FTS5 ``snippet()`` window centred on the
        # matched terms (built in ``_build_bm25_query``), so an agent can see
        # WHY the chunk matched instead of a fixed prefix of the chunk opening.
        # ``or ""`` guards the rare contentless-FTS row that yields NULL.
        snippet = str(row["snippet"] or "")
        # MM-3 — surface per-page citation. Defensive on legacy rows that
        # may pre-date the source_page column.
        raw_page: Any = None
        try:
            raw_page = row["source_page"]
        except (KeyError, IndexError):
            raw_page = None
        return {
            "file": str(row["path"]),
            "title": str(row["title"] or ""),
            "snippet": snippet,
            "score": score,
            "collection": str(row["collection"]),
            "source_page": int(raw_page) if isinstance(raw_page, int) else None,
        }

    def search_fts(
        self,
        query: str,
        collections: list[str] | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Run FTS5 query against documents_fts. Returns [] on any failure."""
        from kairix.core.search.bm25 import _build_bm25_query, _normalise_fts_query

        if not query or not query.strip():
            return []

        fts_query = _normalise_fts_query(query)
        if not fts_query:
            return []

        try:
            db = self._opener(Path(self._db_path))
            db.row_factory = sqlite3.Row
        except Exception as e:
            logger.warning("SQLiteDocumentRepository.search_fts: cannot open DB — %s", e)
            return []

        sql, params = _build_bm25_query(fts_query, collections, limit)

        try:
            rows = db.execute(sql, params).fetchall()
        except sqlite3.OperationalError as e:
            self._log_fts_operational_error(e)
            db.close()
            return []
        except Exception as e:
            logger.warning("SQLiteDocumentRepository.search_fts: FTS query failed — %s", e)
            db.close()
            return []

        results = [self._row_to_search_result(row) for row in rows]
        db.close()
        return results

    def get_by_path(self, path: str) -> dict[str, Any] | None:
        """Look up a document by its path. Returns None if not found."""
        try:
            db = self._opener(Path(self._db_path))
            db.row_factory = sqlite3.Row
            row = db.execute(
                "SELECT d.path, d.collection, d.title, d.hash, COALESCE(c.doc, '') AS content "
                "FROM documents d LEFT JOIN content c ON d.hash = c.hash "
                "WHERE d.path = ? AND d.active = 1 LIMIT 1",
                (path,),
            ).fetchone()
            db.close()
            if row is None:
                return None
            return dict(row)
        except (sqlite3.Error, OSError) as e:
            logger.warning("SQLiteDocumentRepository.get_by_path: %s", e)
            return None

    def get_chunk_dates(self, paths: list[str]) -> dict[str, str]:
        """Return {path: chunk_date} for paths that have a chunk_date.

        Delegates to the per-instance LRU cache keyed on ``frozenset(paths)``
        so that overlapping result sets (the common case when the BM25 and
        vector legs return many of the same hits across concurrent queries)
        do not repeatedly acquire the SQLite WAL reader lock.

        Order-independent: ``["a", "b"]`` and ``["b", "a"]`` resolve to the
        same cache entry. The empty-path short-circuit stays here rather
        than in the cached call so we never waste a cache slot on it.
        """
        if not paths:
            return {}
        return self._chunk_dates_cache(frozenset(paths))

    def _get_chunk_dates_uncached(self, paths: frozenset[str]) -> dict[str, str]:
        """SQL backend for :meth:`get_chunk_dates`. Only called on cache miss.

        GH #409 — uses ``WHERE d.path_canonical IN (?, ?, ...)`` against the
        ``idx_documents_path_canonical`` index. The prior implementation used
        ``LIKE '%suffix'`` to tolerate callers passing collection-relative
        paths, which forced a full table scan on every call (14s p50 on
        1.1M rows in production). ``path_canonical`` is a virtual generated
        column (``GENERATED ALWAYS AS (path) VIRTUAL``) so callers must
        now pass the same path shape stored in ``documents.path`` — i.e.
        the value returned by BM25/vector backends' ``r.path`` field.
        """
        # Materialise once so the SQL parameter list and the placeholder
        # generator iterate the same elements in the same order.
        path_list = list(paths)
        placeholders = ",".join("?" * len(path_list))
        try:
            db = self._opener(Path(self._db_path))
            try:
                # F63-bounded: IN (?,?,...) cardinality is capped by len(path_list), bounded by retrieval-config limits.
                rows = db.execute(
                    f"SELECT d.path, cv.chunk_date "
                    f"FROM content_vectors cv "
                    f"JOIN documents d ON d.hash = cv.hash "
                    f"WHERE cv.chunk_date IS NOT NULL "
                    f"AND d.path_canonical IN ({placeholders})",
                    path_list,
                ).fetchall()
            finally:
                db.close()
        except (sqlite3.Error, OSError) as e:
            logger.warning("SQLiteDocumentRepository.get_chunk_dates: %s", e)
            return {}

        result: dict[str, str] = {}
        for path, chunk_date in rows:
            result[path] = chunk_date
        return result

    def clear_chunk_dates_cache(self) -> None:
        """Drop all cached chunk-date entries.

        Call this after any mutation that can change the answer to a prior
        ``get_chunk_dates`` query (e.g. ``kairix embed`` rewrites
        ``content_vectors.chunk_date``). Also used by tests to verify that
        ``cache_clear`` correctly resets state.
        """
        self._chunk_dates_cache.cache_clear()

    def insert_or_update(
        self,
        path: str,
        collection: str,
        title: str,
        content: str,
        content_hash: str,
    ) -> None:
        """Insert or update a document and its content."""
        try:
            db = self._opener(Path(self._db_path))
            try:
                db.execute(
                    "INSERT OR REPLACE INTO content (hash, doc) VALUES (?, ?)",
                    (content_hash, content),
                )
                db.execute(
                    "INSERT INTO documents (collection, path, title, hash, active) "
                    "VALUES (?, ?, ?, ?, 1) "
                    "ON CONFLICT(collection, path) DO UPDATE SET "
                    "title = excluded.title, hash = excluded.hash, active = 1",
                    (collection, path, title, content_hash),
                )
                db.commit()
            finally:
                db.close()
        except (sqlite3.Error, OSError) as e:
            logger.warning("SQLiteDocumentRepository.insert_or_update: %s", e)
