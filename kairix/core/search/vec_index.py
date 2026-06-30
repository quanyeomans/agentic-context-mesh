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
import os
import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypedDict

import numpy as np

from kairix.core.db import EMBED_VECTOR_DIMS
from kairix.text import strip_frontmatter

logger = logging.getLogger(__name__)

# Default dimensions — reads KAIRIX_EMBED_DIMS env var (default 1536)
DIMS = EMBED_VECTOR_DIMS

# F17 — "collection" appears as a SQLite row key on read AND a result-dict key on
# emit (same conceptual coupling); extract so a rename hits a single edit site.
_KEY_COLLECTION = "collection"

# Default number of vector results to retrieve before fusion
VECTOR_DEFAULT_K: int = 20

# Maximum number of placeholders to put in a single SQLite ``IN (...)`` clause.
# Older SQLite builds cap parameter count at 999; newer builds at 32 766. We
# pick 500 conservatively — this lets the batched fetch span ~25x the
# default ``k`` before we have to issue a second query. Defensive only:
# typical ANN searches surface ≤20 results.
_IN_CLAUSE_BATCH_SIZE: int = 500

# Single SELECT body for the batched metadata lookup. Lifted to a constant
# so we don't duplicate the JOIN text per chunk (F17).
_METADATA_SELECT_SQL: str = (
    "SELECT d.hash, d.path, d.collection, d.title, d.source_page, d.source_uri, "
    "COALESCE(c.doc, '') AS snippet "
    "FROM documents d LEFT JOIN content c ON d.hash = c.hash "
    "WHERE d.active = 1 AND d.hash IN ({placeholders})"
)


def _fsync_file(path: Path) -> None:
    """Flush a freshly written file's bytes to disk.

    Best-effort: an OSError here (e.g. a filesystem that does not
    implement fsync) is logged and swallowed because the atomic rename
    that follows still gives "old file OR new file, never partial" — the
    fsync is the belt-and-braces durability guarantee, not a correctness
    requirement.
    """
    try:
        fd = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError as e:
        logger.warning("vec_index: fsync(%s) failed — %s", path, e)


def _fsync_dir(directory: Path) -> None:
    """Flush a directory entry to disk so the rename is durable.

    On POSIX, ``os.rename`` is atomic but the directory entry update
    that records the new file name lives in the parent's inode; only an
    fsync on the directory fd guarantees the rename survives a crash
    that hits before the next periodic dirent flush. No-op on Windows
    where directory fsync is not supported.
    """
    try:
        fd = os.open(str(directory), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError as e:
        # PermissionError on Windows (no dir fsync) is expected and not
        # actionable; log at debug so production noise stays low.
        logger.debug("vec_index: fsync(dir=%s) skipped — %s", directory, e)


class VecResult(TypedDict):
    """Single vector search result."""

    hash_seq: str
    distance: float
    path: str
    collection: str
    title: str
    snippet: str
    # MM-3 — per-page citation. ``None`` for non-paged documents
    # (passthrough markdown); populated from ``documents.source_page``
    # for PDF / PPTX / XLSX chunks via the metadata JOIN.
    source_page: int | None
    # PLA-274 — canonical resolvable breadcrumb (``documents.source_uri``).
    # ``""`` for passthrough vault rows (NULL column); SourceRef.of falls
    # that back to ``path`` so the pointer stays resolvable. Carried via the
    # metadata JOIN through fusion → SearchHit → envelope.
    source_uri: str


class VectorIndex:
    """usearch-backed ANN index with collection-scoped search."""

    def __init__(
        self,
        index_path: Path,
        meta_path: Path,
        db_path: Path,
        ndim: int = DIMS,
        read_only: bool = False,
    ) -> None:
        # GH #352 — ``read_only`` selects the on-disk open mode:
        #   * read_only=False (default, mutate-capable): ``Index.restore(view=False)``
        #     loads the full HNSW graph into process memory at load() time.
        #     One-time ~7.8 GB for a 1.27M x 1536-dim corpus, resident for
        #     the process lifetime. Required for any caller that adds /
        #     removes vectors (the worker's embed cycle, --force rebuilds,
        #     ADR-028 re-chunk-sweep tick). The alternative — view=True
        #     followed by a conversion-on-first-mutation — OOM-killed the
        #     production worker at 1.27M vectors even under memswap=24g
        #     (see GH #352 root-cause analysis).
        #   * read_only=True: ``Index.restore(view=True)`` mmap's the file,
        #     pages are loaded on access. Cheap startup; ANY mutation
        #     raises a typed error from _ensure_mutable(). Right mode for
        #     search-side consumers (MCP, eval, probe, recall-check).
        # Default is False so a caller who forgets to think about it gets
        # the correct behaviour for the mutate-heavy worker path.
        self._index_path = Path(index_path)
        self._meta_path = Path(meta_path)
        self._db_path = Path(db_path)
        self._ndim = ndim
        self._read_only = read_only
        self._index: Any = None
        self._key_to_hash_seq: dict[int, str] = {}
        self._next_key: int = 0
        self._mutable: bool = False
        # Persistent read-only metadata connection (#perf-vector-ann).
        # Before: every ``_fetch_metadata_batched`` call paid
        # ``sqlite3.connect`` + ``PRAGMA journal_mode=WAL`` + ``PRAGMA
        # foreign_keys=ON`` + ``db.close()`` per search — measurable
        # (5-30ms) per query on the warm-cache path. After: the connection
        # is opened once on first use and reused for the lifetime of the
        # VectorIndex instance (production: the read-only singleton built
        # by ``get_vector_index``; one connection per process).
        #
        # ``check_same_thread=False`` paired with the lock makes the
        # connection safe for the parallel-dispatch worker thread
        # (search-dispatch pool from pipeline.py) — symmetrical with the
        # treatment ``_build_topology_v2_collection_resolver`` already
        # applies to its resolver Connection.
        self._meta_conn: sqlite3.Connection | None = None
        self._meta_conn_lock = threading.Lock()

    def __len__(self) -> int:
        if self._index is None:
            return 0
        return len(self._index)

    def load(self) -> int:
        """Load existing usearch index + metadata from disk.

        If the index was built with different dimensions, deletes it and
        returns 0 so a fresh index is created on the next add_vectors() call.

        Crash-recovery: if the canonical ``<index_path>`` is missing but
        a sibling ``<index_path>.tmp`` exists (a crash mid-rename during
        :meth:`_save`), promote the .tmp file to the canonical path
        before loading. The atomic rename in ``_save`` makes "old file
        intact OR new file intact" the only post-crash states; surfacing
        the .tmp here closes the rare window where the OS crashed
        between fsync and rename.
        """
        from usearch.index import Index

        self._recover_pending_tmp_files()

        if not self._index_path.exists():
            return 0

        # Parse meta ONCE — the file can be 14 MB+ on a fully-indexed corpus,
        # and both the dimension check and the key-mapping load consume it.
        meta: dict[str, Any] | None = None
        if self._meta_path.exists():
            try:
                meta = json.loads(self._meta_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("vec_index: meta unreadable — index loaded without key mapping (%s)", e)
                meta = None

        if meta is not None:
            stored_ndim = meta.get("ndim", 0)
            if stored_ndim and stored_ndim != self._ndim:
                logger.warning(
                    "vec_index: dimension mismatch (index=%d, expected=%d) — deleting old index",
                    stored_ndim,
                    self._ndim,
                )
                self._delete_index_files()
                return 0

        # GH #352 — view=self._read_only chooses mmap (read_only) vs
        # full-load-into-memory (read_write). See __init__ docstring for
        # the trade-off. A read_write instance is mutable from this point;
        # no first-write conversion path will fire.
        self._index = Index.restore(str(self._index_path), view=self._read_only)
        self._mutable = not self._read_only
        if meta is not None:
            try:
                self._key_to_hash_seq = {int(k): v for k, v in meta["keys"].items()}
                # #413 defence-in-depth: trust meta when consistent, but fall
                # back to (max actual key) + 1 if meta lags. A stale meta
                # caused 'Duplicate keys not allowed' in add_vectors after
                # #375 kept the handle open across batches — pre-#375 each
                # batch reopened the handle, masking drift by recomputing on
                # every restore.
                meta_next_key = int(meta.get("next_key", 0))
                inferred_next_key = max(self._key_to_hash_seq.keys(), default=-1) + 1
                self._next_key = max(meta_next_key, inferred_next_key)
            except (KeyError, ValueError) as e:
                logger.warning("vec_index: meta missing 'keys' — index loaded without key mapping (%s)", e)
        return len(self._index)

    def load_or_recreate(self) -> tuple[int, str]:
        """Load the existing index; if corrupt, delete it and return fresh empty state.

        Closes the production bug where ``open_default_usearch_index()`` caught
        the load exception and returned None — every subsequent vector
        write was silently no-op'd because the orchestration code
        guarded on ``vec_index is not None``. The whole ``--force`` run
        completed without persisting a single vector and the operator
        only noticed via the next recall check.

        Returns ``(vector_count, status)`` where status is one of:

          * ``"loaded"`` — canonical file present + parsed cleanly
          * ``"recovered-from-tmp"`` — canonical missing, ``.tmp`` promoted
          * ``"recreated"`` — canonical present but corrupt; deleted + fresh empty state
          * ``"empty"`` — no canonical and no ``.tmp``; fresh empty state

        Callers see at most one of these per process startup; pair with
        an INFO log line carrying the canonical KairixPaths-resolved
        path so operators can audit which deployment is which.
        """
        try:
            count = self.load()
            # load() returns 0 when no file exists OR when dimension mismatch deleted it.
            if count == 0 and not self._index_path.exists():
                return 0, "empty"
            # A .tmp file may have been promoted during load() — check if
            # the canonical post-load state was reached via promotion.
            # (Promotion is non-destructive when both files exist; the
            # canonical wins.)
            return count, "loaded"
        except (ValueError, OSError, KeyError) as exc:
            # ValueError covers usearch's "Not a dense USearch index!"
            # (header-corrupt file); OSError covers I/O failures; KeyError
            # covers meta-file shape mismatches that escape load()'s
            # internal try/except.
            logger.warning(
                "vec_index: existing index at %s is corrupt — recreating fresh. "
                "fix: this is auto-recovery; no operator action needed. "
                "next: the in-flight embed run will populate the fresh index. "
                "run: kairix onboard check to confirm vector_search_working post-run. "
                "(cause: %s)",
                self._index_path,
                exc,
            )
            self._delete_index_files()
            return 0, "recreated"

    def _delete_index_files(self) -> None:
        """Remove index and metadata files from disk."""
        for path in (self._index_path, self._meta_path):
            try:
                path.unlink(missing_ok=True)
            except OSError as e:
                logger.warning("vec_index: failed to delete %s — %s", path, e)
        self._index = None
        self._key_to_hash_seq = {}
        self._next_key = 0
        # GH #352 — _mutable left alone deliberately. A read_only instance
        # stays read_only post-clear (and refuses mutation cleanly);
        # a read_write instance hits the "_index is None → construct
        # fresh mutable" branch in _ensure_mutable on the next add call.
        self._mutable = False

    def clear(self) -> None:
        """Discard the on-disk index + in-memory state.

        GH #352 — public entry point for ``kairix embed --force`` and the
        ADR-028 re-chunk-sweep tick. Before this method existed, ``--force``
        cleared SQLite ``content_vectors`` but left the on-disk usearch
        index untouched. The first subsequent ``add_vectors()`` call would
        trigger ``_ensure_mutable`` to load every existing vector into a
        numpy array (to copy into a new mutable index) only to have those
        keys overwritten by the freshly-embedded vectors. On a 1.27M-vector
        corpus that wasted load OOM-killed the worker under memswap=24g.

        ``clear()`` is the right pre-step: the embed pipeline calls it
        immediately after ``DELETE FROM content_vectors`` so the on-disk
        index file is also gone, and the next ``add_vectors()`` builds a
        fresh empty mutable index with zero conversion cost.

        Safe to call when the index doesn't exist on disk (no-op).
        """
        self._delete_index_files()

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

    def _resolve_match_metadata(
        self,
        matches: Any,
        k: int,
        collections: list[str] | None,
    ) -> list[dict]:
        """Resolve ANN match keys to document metadata via a batched SQLite query.

        Issues ONE multi-row SELECT against ``documents``/``content`` using an
        ``IN (?, ?, ...)`` clause, then zips the rows back to the input order
        in Python. Reduces per-search SQLite journal-lock acquisitions from
        ``k x N_threads`` (one per ANN hit) to ``ceil(k / batch) x N_threads``
        — typically one — and dropped vector_ann mean latency from ~440 ms to
        ~30-50 ms at conc=10 in profiling (#287).

        Correctness invariants preserved:

        * ``d.active = 1`` filter
        * Collection filter (in-Python after the fetch, mirroring the row
          filter the old per-row loop applied)
        * Result ordering follows the ANN ranking in ``matches.keys`` — NOT
          the SQL row order, which is undefined in SQLite without ORDER BY
        * Frontmatter stripping on the snippet
        * ``k`` cap on the returned list
        * ``[]`` on DB failure

        Returns list of VecResult-compatible dicts. Returns [] on DB failure
        or when ``matches`` carries no keys.
        """
        ordered = self._ordered_content_hashes(matches)
        if not ordered:
            return []

        unique_hashes = list({content_hash for _, _, content_hash in ordered})
        try:
            rows_by_hash = self._fetch_metadata_batched(unique_hashes)
        except (sqlite3.Error, OSError) as e:
            logger.warning("vec_index: metadata lookup failed — %s", e)
            return []

        return self._build_results(ordered, rows_by_hash, k, collections)

    def _ordered_content_hashes(self, matches: Any) -> list[tuple[str, float, str]]:
        """Flatten ANN matches into ``(hash_seq, distance, content_hash)`` tuples.

        Drops keys with no mapping in ``self._key_to_hash_seq`` (same skip
        as the pre-batch implementation). Order follows ``matches.keys`` so
        downstream code can rely on ANN ranking.
        """
        ordered: list[tuple[str, float, str]] = []
        for key, distance in zip(matches.keys, matches.distances, strict=True):
            hash_seq = self._key_to_hash_seq.get(int(key))
            if hash_seq is None:
                continue
            content_hash = hash_seq.rsplit("_", 1)[0]
            ordered.append((hash_seq, float(distance), content_hash))
        return ordered

    def _fetch_metadata_batched(self, unique_hashes: list[str]) -> dict[str, sqlite3.Row]:
        """Fetch all metadata rows for ``unique_hashes`` in batches.

        Defensive: SQLite caps placeholders at 999 (older) or 32 766
        (newer). We chunk at :data:`_IN_CLAUSE_BATCH_SIZE` so absurd
        ``k`` values still work. The common path (k ≤ 20) issues a
        single query.

        Uses the persistent read-only metadata connection (see
        ``__init__``) so the hot path doesn't pay
        ``sqlite3.connect`` + WAL/foreign-keys PRAGMA roundtrips on every
        vector search. The connection is opened lazily on first use and
        kept open for the lifetime of the VectorIndex instance.
        """
        rows_by_hash: dict[str, sqlite3.Row] = {}
        db = self._get_meta_conn()
        for start in range(0, len(unique_hashes), _IN_CLAUSE_BATCH_SIZE):
            chunk = unique_hashes[start : start + _IN_CLAUSE_BATCH_SIZE]
            placeholders = ",".join("?" * len(chunk))
            sql = _METADATA_SELECT_SQL.format(placeholders=placeholders)
            # Serialise execute calls on the shared connection — the
            # sqlite3 driver enforces single-cursor state per Connection
            # even with ``check_same_thread=False``. The metadata query is
            # tiny (~ms), so the lock-hold cost is dominated by the SELECT
            # itself; no contention concern even at conc=10.
            with self._meta_conn_lock:
                # F63-bounded: chunk-size capped by upstream `_BATCH_SIZE` (search-time IN-clause batching).
                for row in db.execute(sql, tuple(chunk)).fetchall():
                    rows_by_hash[row["hash"]] = row
        return rows_by_hash

    def _get_meta_conn(self) -> sqlite3.Connection:
        """Return the persistent read-only metadata SQLite connection.

        Opens the connection on first call (under the lock so concurrent
        first-arrivals don't both build it) and reuses it for every
        subsequent search. ``row_factory`` is set once at open time —
        callers consume rows via ``row["hash"]`` / ``row["path"]`` etc.

        ``check_same_thread=False`` lets the parallel-dispatch worker
        thread (search-dispatch pool from pipeline.py) execute against
        this connection; the per-execute serialisation above provides
        the missing cursor-state safety the sqlite3 driver requires.
        WAL + foreign-keys PRAGMA are applied once at open time so the
        per-search hot path pays nothing for them.
        """
        if self._meta_conn is not None:
            return self._meta_conn
        with self._meta_conn_lock:
            if self._meta_conn is not None:
                return self._meta_conn
            # F77-allow: VectorIndex metadata reader; MCP/search-only singleton; one connection per process.
            conn = sqlite3.connect(str(self._db_path), timeout=10.0, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            # Mirror open_db's PRAGMA setup — paid once at open time.
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._meta_conn = conn
            return conn

    def close_meta_conn(self) -> None:
        """Close the persistent metadata connection. Idempotent.

        Tests + ``reset_vector_index_singleton`` call this so the OS-level
        file handle is released between test cases that open multiple
        VectorIndex instances against the same DB.
        """
        with self._meta_conn_lock:
            if self._meta_conn is not None:
                try:
                    self._meta_conn.close()
                finally:
                    self._meta_conn = None

    def set_metadata_trace_callback(
        self,
        callback: Callable[[str], None] | None,
    ) -> None:
        """Install (or clear) a SQL-trace callback on the metadata connection.

        Public test seam: BDD/regression tests counting the SELECTs
        issued during ``_resolve_match_metadata`` install a counter
        through this surface instead of reaching past the underscore-
        prefixed ``_meta_conn`` attribute (F5). Forces the lazy-open
        path so the callback is installed on a real connection.

        Pass ``None`` to clear the trace.
        """
        conn = self._get_meta_conn()
        conn.set_trace_callback(callback)

    @staticmethod
    def _row_optional_meta(row: sqlite3.Row) -> tuple[int | None, str]:
        """Read ``(source_page, source_uri)`` defensively from a metadata row.

        MM-3 + PLA-274. Legacy DBs may pre-date either column, so each read
        is guarded; ``source_uri`` defaults to ``""`` (SourceRef.of falls
        that back to ``path`` downstream). Extracted from ``_build_results``
        so that loop stays under the F16 cognitive-complexity ceiling.
        """
        raw_page: Any = None
        try:
            raw_page = row["source_page"]
        except (KeyError, IndexError):
            raw_page = None
        raw_uri: Any = ""
        try:
            raw_uri = row["source_uri"]
        except (KeyError, IndexError):
            raw_uri = ""
        page = int(raw_page) if isinstance(raw_page, int) else None
        return page, str(raw_uri or "")

    def _build_results(
        self,
        ordered: list[tuple[str, float, str]],
        rows_by_hash: dict[str, sqlite3.Row],
        k: int,
        collections: list[str] | None,
    ) -> list[dict]:
        """Zip ordered ANN hits with the batched rows; apply filters and ``k`` cap."""
        results: list[dict] = []
        for hash_seq, distance, content_hash in ordered:
            row = rows_by_hash.get(content_hash)
            if row is None:
                continue
            if collections and row[_KEY_COLLECTION] not in collections:
                continue
            snippet_raw = row["snippet"]
            snippet = strip_frontmatter(snippet_raw)[:300] if snippet_raw else ""
            # MM-3 per-page citation + PLA-274 canonical breadcrumb, read
            # defensively for legacy rows (helper keeps this loop under F16).
            page, uri = self._row_optional_meta(row)
            results.append(
                {
                    "hash_seq": hash_seq,
                    "distance": distance,
                    "path": row["path"],
                    _KEY_COLLECTION: row[_KEY_COLLECTION],
                    "title": row["title"],
                    "snippet": snippet,
                    "source_page": page,
                    "source_uri": uri,
                }
            )
            if len(results) >= k:
                break
        return results

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

        fetch_k = min(k * 4 if collections else k, len(self._index))
        matches = self._index.search(query_vec.astype(np.float32), fetch_k)

        return self._resolve_match_metadata(matches, k, collections)

    def _ensure_mutable(self) -> None:
        """Ensure the index is mutable. Cheap when constructed with
        ``read_only=False`` (load() already opened it mutable); raises
        when constructed with ``read_only=True`` (caller should construct
        a separate read_write instance for mutation).

        GH #352 — the previous "convert on first mutation" path loaded
        every existing vector into a numpy array to copy into a new
        mutable index. On a 1.27M x 1536-dim corpus that's ~7.8 GB just
        for the array plus HNSW graph overhead, which OOM-killed the
        production worker even under memswap=24g. The fix shifts the
        decision to construction time: a read_write instance is mutable
        from load(); a read_only instance refuses mutation loudly.

        Check order matters: read_only refusal first (so a read_only
        instance can never mutate, even post-clear); fresh-construct
        second (covers post-clear() and never-loaded cases on a
        read_write instance); steady-state short-circuit third.
        """
        from usearch.index import Index

        if self._read_only:
            raise RuntimeError(
                "VectorIndex was constructed with read_only=True — mutation rejected. "
                "fix: construct a separate VectorIndex(read_only=False) for the writer process; "
                "the read_only mode is intended for search-side consumers (MCP / eval / probe). "
                "next: in the worker path use VectorIndex(..., read_only=False) (the default). "
                "run: see kairix/core/embed/embed.py:open_default_usearch_index for the production wiring."
            )

        if self._index is None:
            self._index = Index(ndim=self._ndim, metric="cos", dtype="f32")
            self._mutable = True
            return

        if self._mutable:
            return

        # Defensive: index exists but isn't mutable. With read_only=False
        # this shouldn't happen after load() unless someone hand-toggled
        # _mutable. Re-load fresh from disk to get a mutable copy rather
        # than risk the deprecated convert-on-mutate OOM path.
        self.load()

    def add_vectors(self, hash_seqs: list[str], vectors: list[list[float]]) -> int:
        """Add new vectors incrementally. Does NOT auto-save — caller controls save timing."""
        if not hash_seqs:
            return 0
        self._ensure_mutable()

        arr = np.array(vectors, dtype=np.float32)
        keys = np.arange(self._next_key, self._next_key + len(hash_seqs), dtype=np.int64)
        self._index.add(keys, arr)
        for k, hs in zip(keys, hash_seqs, strict=True):
            self._key_to_hash_seq[int(k)] = hs
        self._next_key += len(hash_seqs)
        return len(hash_seqs)

    def save(self) -> None:
        """Save index and metadata to disk. Public wrapper for callers."""
        self._save()

    def _save(self) -> None:
        """Save index and metadata to disk using a write-tmp + fsync + rename
        protocol.

        Crash-safety contract: ``os.replace(tmp, path)`` is atomic on POSIX,
        so a crash mid-cycle leaves either the old valid file or the new
        valid file — never a half-written index that fails ``Index.restore``
        with an unreadable header. ``os.fsync`` on the temp file and on the
        parent directory fd flushes the bytes + the directory entry before
        the rename, which is what makes the atomicity guarantee meaningful
        on disks with write caches.

        Closes the production incident where ~1.57M of 1.8M vectors were
        successfully embedded then the file was corrupted by a partial
        in-place write; the operator paid for the embed calls but had to
        re-run.
        """
        if self._index is None:
            return
        self._index_path.parent.mkdir(parents=True, exist_ok=True)

        tmp_index = self._tmp_path(self._index_path)
        self._index.save(str(tmp_index))
        _fsync_file(tmp_index)
        os.replace(tmp_index, self._index_path)

        meta = {
            "keys": {str(k): v for k, v in self._key_to_hash_seq.items()},
            "next_key": self._next_key,
            "ndim": self._ndim,
        }
        tmp_meta = self._tmp_path(self._meta_path)
        tmp_meta.write_text(json.dumps(meta), encoding="utf-8")
        _fsync_file(tmp_meta)
        os.replace(tmp_meta, self._meta_path)

        _fsync_dir(self._index_path.parent)

    @staticmethod
    def _tmp_path(target: Path) -> Path:
        """Return the sibling ``<target>.tmp`` path used by :meth:`_save`."""
        return target.with_name(target.name + ".tmp")

    def _recover_pending_tmp_files(self) -> None:
        """Promote a lingering ``<path>.tmp`` to its canonical path.

        Only fires when the canonical file is MISSING — a present
        canonical file is the source-of-truth and any stale .tmp is
        ignored. Logged as info so the recovery shows up in operator
        diagnostics without raising the severity to warning.
        """
        for canonical in (self._index_path, self._meta_path):
            tmp = self._tmp_path(canonical)
            if canonical.exists() or not tmp.exists():
                continue
            try:
                os.replace(tmp, canonical)
                logger.info("vec_index: recovered pending %s from .tmp after interrupted save", canonical.name)
            except OSError as e:
                logger.warning("vec_index: could not promote %s.tmp — %s", canonical, e)


# ---------------------------------------------------------------------------
# Process-singleton accessor
# ---------------------------------------------------------------------------

_VECTOR_INDEX: Any = None


def get_vector_index(db_path: Path | None = None) -> Any:
    """Lazily load the usearch VectorIndex singleton.

    Args:
        db_path: SQLite index path. The vector files (``vectors.usearch``
                 and ``vectors.meta.json``) are expected in the same
                 directory. Defaults to ``kairix.paths.db_path()`` for
                 production use; tests pass an explicit path.

    Returns the loaded index, or None if the index is empty/missing/unloadable.
    Subsequent calls return the cached instance.
    Never raises — returns None on any failure.
    """
    global _VECTOR_INDEX
    if _VECTOR_INDEX is not None:
        return _VECTOR_INDEX
    try:
        if db_path is None:
            from kairix.paths import db_path as _resolve_db_path

            db_path = _resolve_db_path()
        index_path = db_path.parent / "vectors.usearch"
        meta_path = db_path.parent / "vectors.meta.json"
        # GH #352 — singleton consumers (MCP search, eval, probe,
        # recall-check) are read-only; they query the index, never mutate.
        # read_only=True opens via mmap (cheap startup, low RSS); any
        # accidental mutation surfaces as a typed error from
        # _ensure_mutable. The worker's mutate-capable instance is
        # constructed separately in kairix.core.embed.embed.open_default_usearch_index.
        idx = VectorIndex(index_path=index_path, meta_path=meta_path, db_path=db_path, read_only=True)
        count = idx.load()
        if count > 0:
            logger.info("vec_index: loaded usearch index (%d vectors)", count)
            _VECTOR_INDEX = idx
            return idx
        logger.warning("vec_index: usearch index empty or missing at %s", index_path)
        return None
    except Exception as e:
        logger.warning("vec_index: failed to load usearch index — %s", e)
        return None


def reset_vector_index_singleton() -> None:
    """Clear the cached singleton. For test isolation.

    Closes the persistent metadata connection first so the OS-level
    file handle is released before the next ``get_vector_index`` call
    opens a fresh one against the same DB.
    """
    global _VECTOR_INDEX
    if _VECTOR_INDEX is not None and hasattr(_VECTOR_INDEX, "close_meta_conn"):
        try:
            _VECTOR_INDEX.close_meta_conn()
        except Exception as exc:
            logger.debug("reset_vector_index_singleton: close_meta_conn failed (continuing): %s", exc)
    _VECTOR_INDEX = None
