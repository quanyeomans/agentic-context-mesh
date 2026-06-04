"""Restart-resilient embed cache for the embed roundtrip.

Lives in :mod:`kairix.transport.cache` — the universal endpoint
response cache. See docs/architecture/provider-plugin-architecture.md
for the three-layer split (core / transport / providers); this module
is the transport-layer cache that sits in front of every provider's
embed call, not a domain concern of any single provider.

LRU bounded by entry count + per-entry max age. Thread-safe (kairix
MCP serves multiple agents concurrently). Cache key is the normalised
query text — the same text embeds to the same vector regardless of
which agent / scope / collection asked for it, so this cache fills
the gap left by the result cache (#281), which keys on the full
``(query, scope, agent, collections)`` four-tuple and therefore misses
when two agents ask the same question from different scopes.

Hit value: ~5 ms memory lookup vs ~250-500 ms embed roundtrip (and
~1 s at conc=10). Even at conc=10 today's p95 = 3107 ms because
fresh queries still pay the full embed cost; this cache aims to take
that cost off the hot path whenever the SAME text has been embedded
recently.

Persistence (#391)
------------------

Construct with ``path=...`` to back the in-memory LRU with a SQLite
file on disk. ``put`` is write-through (INSERT OR REPLACE) so the next
process restart finds the entries already populated; ``__init__``
replays the on-disk rows into the in-memory ``OrderedDict`` so the
first ``get`` after restart serves from RAM rather than reloading
through SQLite. Construction with ``path=None`` keeps the original
in-memory-only behaviour for tests and ad-hoc instances.

Design notes:

- ``OrderedDict`` backs the LRU. ``move_to_end(key)`` promotes on
  access; ``popitem(last=False)`` evicts the oldest entry when the
  bound is exceeded. Same shape as
  :class:`kairix.core.search.query_cache.QueryResultCache`.
- Each entry stores ``(insertion_time_s, embedding)``. ``get`` checks
  age at read time so a stale-but-not-yet-evicted entry is reported as
  a miss (operator-facing stats stay honest).
- A single :class:`threading.RLock` guards all reads + writes. The
  cost of contention is dwarfed by the cost of the embed roundtrip
  the cache avoids on a hit.
- Default max-age is 30 min — longer than the result cache's 5 min
  because embed vectors depend only on the model + text, not on
  changing vault state. Two agents asking the same question 20 min
  apart will share an embedding even though their result sets differ.
- :func:`normalise_query` is re-used from
  :mod:`kairix.core.search.query_cache` rather than re-defined — the
  result cache and the embed cache MUST agree on what "same text"
  means, or a result-cache miss could re-embed text that's already
  in the embed cache (and vice versa).
"""

from __future__ import annotations

import logging
import sqlite3
import struct
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# Re-export normalise_query so consumers (tests, integration code) can
# import it from a single canonical location regardless of which cache
# layer they are operating on. The result cache and the embed cache
# share the same normalisation rules — by re-exporting we anchor that
# invariant in code.
from kairix.core.search.query_cache import normalise_query

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_MAX_AGE_S",
    "DEFAULT_MAX_ENTRIES",
    "EmbedCache",
    "EmbedCacheStats",
    "get_embed_cache",
    "install_embed_cache",
    "normalise_query",
    "reset_embed_cache",
]

DEFAULT_MAX_ENTRIES = 1000
DEFAULT_MAX_AGE_S = 1800.0  # 30 minutes — embeddings depend on model + text only.

# SQLite schema for the on-disk persistence layer (#391). One row per
# normalised-query key; the vector is stored as raw little-endian f32
# bytes (struct-packed) to keep the schema dependency-free of numpy at
# the transport layer (numpy lives in kairix.core only). ``inserted_at``
# is the same wall-clock seconds the in-memory layer uses so the
# replay-on-startup path can re-apply the age check without translation.
_TABLE = "embed_cache"
_CREATE_SQL = (
    f"CREATE TABLE IF NOT EXISTS {_TABLE} ("
    "  key          TEXT PRIMARY KEY,"
    "  inserted_at  REAL NOT NULL,"
    "  vector       BLOB NOT NULL"
    ")"
)
_UPSERT_SQL = f"INSERT OR REPLACE INTO {_TABLE} (key, inserted_at, vector) VALUES (?, ?, ?)"
_SELECT_ALL_SQL = f"SELECT key, inserted_at, vector FROM {_TABLE} ORDER BY inserted_at ASC LIMIT ?"
_DELETE_KEY_SQL = f"DELETE FROM {_TABLE} WHERE key = ?"
_TRUNCATE_SQL = f"DELETE FROM {_TABLE}"


def _encode_vector(embedding: list[float]) -> bytes:
    """Pack a float list as little-endian f32 bytes for SQLite storage."""
    return struct.pack(f"<{len(embedding)}f", *embedding)


def _decode_vector(blob: bytes) -> list[float]:
    """Unpack little-endian f32 bytes back into a Python float list."""
    count = len(blob) // 4
    return list(struct.unpack(f"<{count}f", blob))


@dataclass(frozen=True)
class EmbedCacheStats:
    """Read-only snapshot of cache state for the onboard envelope.

    ``hit_rate`` is the convenience derivative used by the onboard
    JSON envelope; ``0.0`` when no queries have run yet so operators
    see "no data" rather than NaN.
    """

    size: int
    hits: int
    misses: int
    evictions: int
    oldest_entry_age_s: float
    hit_rate: float  # 0.0 to 1.0


class EmbedCache:
    """Bounded LRU cache keyed on normalised query text → embedding vector.

    Mirrors :class:`kairix.core.search.query_cache.QueryResultCache`
    exactly in shape, but keyed on just the text — so two agents asking
    the same question from different scopes / collections share the
    expensive embed roundtrip even though they don't share a final
    search result.

    Thread safety: a single :class:`threading.RLock` guards all reads +
    writes. Contention cost here is negligible vs the embed roundtrip
    we avoid on every hit.
    """

    def __init__(
        self,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        max_age_s: float = DEFAULT_MAX_AGE_S,
        *,
        clock: Callable[[], float] = time.time,
        path: Path | str | None = None,
    ) -> None:
        self._max_entries = max(1, int(max_entries))
        self._max_age_s = float(max_age_s)
        self._entries: OrderedDict[str, tuple[float, list[float]]] = OrderedDict()
        self._lock = threading.RLock()
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        # Public DI seam — tests pass a controllable clock to drive
        # expiry without monkey-patching ``time.time`` inside this module.
        self._clock = clock
        # On-disk persistence layer (#391). When ``path`` is None, the
        # cache stays in-memory-only — keeps the legacy contract for
        # tests and ad-hoc instances. When set, ``put`` is write-through
        # and ``__init__`` replays existing rows into the in-memory LRU.
        self._path: Path | None = Path(path) if path is not None else None
        self._conn: sqlite3.Connection | None = None
        if self._path is not None:
            self._open_and_replay()

    def _open_and_replay(self) -> None:
        """Open the SQLite file (creating it if needed) and replay rows into the LRU.

        The directory is created if missing — operators occasionally
        bind-mount the data dir before the cache file has ever been
        written, and silently failing to create the file would leave
        the operator-visible 0-byte symptom that triggered #391.
        Connection is shared across threads (``check_same_thread=False``)
        because the ``RLock`` already serialises all access at the
        Python level.
        """
        assert self._path is not None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # F77-allow: query-embed cache DB; MCP-only writer; #391.
            conn = sqlite3.connect(str(self._path), check_same_thread=False)
            conn.execute(_CREATE_SQL)
            conn.commit()
            self._conn = conn
        except (OSError, sqlite3.Error) as exc:
            # Disk-layer failure should never crash the embed path — the
            # cache degrades to in-memory-only and logs the cause so the
            # operator sees the warning rather than a silently-broken
            # restart-resilient cache.
            logger.warning(
                "EmbedCache: failed to open persistence file %s — degrading to in-memory-only. cause: %s",
                self._path,
                exc,
            )
            self._conn = None
            return

        # Replay existing rows oldest-first so the in-memory LRU
        # ordering reflects insertion order on disk. Drop expired
        # entries during replay so a long-stopped process can't serve
        # stale embeddings on restart.
        try:
            # F63-bounded: LIMIT capped at self._max_entries (replay never
            # loads more rows than the in-memory LRU can hold; older rows
            # would be evicted anyway).
            cursor = self._conn.execute(_SELECT_ALL_SQL, (self._max_entries,))
            now = self._clock()
            for key, inserted_at, blob in cursor.fetchall():
                if (now - inserted_at) > self._max_age_s:
                    # Drop the expired row on disk so the file doesn't
                    # accumulate stale entries across restarts.
                    self._conn.execute(_DELETE_KEY_SQL, (key,))
                    continue
                vector = _decode_vector(blob)
                self._entries[key] = (inserted_at, vector)
                if len(self._entries) > self._max_entries:
                    evicted_key, _ = self._entries.popitem(last=False)
                    self._conn.execute(_DELETE_KEY_SQL, (evicted_key,))
                    self._evictions += 1
            self._conn.commit()
        except sqlite3.Error as exc:  # pragma: no cover — defensive replay fallback.
            logger.warning(
                "EmbedCache: failed to replay existing rows from %s — starting with empty in-memory cache. cause: %s",
                self._path,
                exc,
            )

    @property
    def path(self) -> Path | None:
        """On-disk persistence path, or ``None`` when in-memory-only."""
        return self._path

    def close(self) -> None:
        """Close the underlying SQLite connection (idempotent).

        Tests call this between cases when constructing multiple caches
        against the same ``tmp_path`` so the OS-level file handle is
        released before the next construction reopens it.
        """
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                finally:
                    self._conn = None

    def get(self, query: str) -> list[float] | None:
        """Return the cached embedding or ``None``. Expired entries miss.

        Promotes the entry to most-recently-used on a successful hit
        so the LRU ordering reflects access, not insertion. Empty /
        whitespace-only queries are reported as misses without
        consulting the table — they never get cached either (see
        :meth:`put`), so this short-circuit keeps the lock window
        tight on that path.
        """
        if not query or not query.strip():
            return None
        key = normalise_query(query)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self._misses += 1
                return None
            inserted_at, value = entry
            if self._is_expired(inserted_at):
                # Drop the expired entry on the floor and report a miss.
                # Operators reading stats want stale reads counted as
                # misses, not hits — re-embedding is the same outcome as
                # serving stale text.
                del self._entries[key]
                self._delete_persisted(key)
                self._misses += 1
                return None
            self._entries.move_to_end(key)
            self._hits += 1
            # Defensive copy so a caller mutating the returned list
            # can't corrupt the cached vector for the next reader.
            return list(value)

    def put(self, query: str, embedding: list[float]) -> None:
        """Insert or refresh an entry. Evicts the oldest when bounded.

        Empty / whitespace-only queries and empty embeddings are NOT
        cached — caching ``[]`` would lock the "embed failed" outcome
        in front of every same-text caller until the entry ages out.

        Write-through to the SQLite persistence layer when ``path``
        was set at construction (#391). On-disk write failure logs
        a warning and keeps the in-memory entry — the next process
        restart loses that entry but production keeps serving from
        memory in the meantime.
        """
        if not query or not query.strip():
            return
        if not embedding:
            return
        key = normalise_query(query)
        with self._lock:
            now = self._clock()
            # Defensive copy so the cache owns its own list and a
            # caller mutating the original argument after put() can't
            # change what we hand out on the next get().
            stored = list(embedding)
            if key in self._entries:
                # Existing key: refresh the timestamp and promote to MRU.
                self._entries[key] = (now, stored)
                self._entries.move_to_end(key)
                self._upsert_persisted(key, now, stored)
                return
            self._entries[key] = (now, stored)
            self._upsert_persisted(key, now, stored)
            if len(self._entries) > self._max_entries:
                evicted_key, _ = self._entries.popitem(last=False)
                self._delete_persisted(evicted_key)
                self._evictions += 1

    def _upsert_persisted(self, key: str, inserted_at: float, embedding: list[float]) -> None:
        """Write a single entry to the SQLite layer. Caller holds the lock."""
        if self._conn is None:
            return
        try:
            with self._conn:
                self._conn.execute(_UPSERT_SQL, (key, inserted_at, _encode_vector(embedding)))
        except sqlite3.Error as exc:  # pragma: no cover — defensive write fallback.
            logger.warning(
                "EmbedCache: persistence write failed for key %r — entry kept in-memory only. cause: %s",
                key,
                exc,
            )

    def _delete_persisted(self, key: str) -> None:
        """Remove a single entry from the SQLite layer. Caller holds the lock."""
        if self._conn is None:
            return
        try:
            with self._conn:
                self._conn.execute(_DELETE_KEY_SQL, (key,))
        except sqlite3.Error as exc:  # pragma: no cover — defensive: stale row on next restart is the only consequence.
            logger.warning(
                "EmbedCache: persistence delete failed for key %r — entry will replay on next restart. cause: %s",
                key,
                exc,
            )

    def stats(self) -> EmbedCacheStats:
        """Return an atomic snapshot of cache state."""
        with self._lock:
            size = len(self._entries)
            oldest_age = 0.0
            if size > 0:
                # OrderedDict keeps insertion order; LRU oldest is the
                # leftmost entry. ``next(iter(...))`` is O(1) so the
                # lock window stays tight.
                oldest_key = next(iter(self._entries))
                oldest_inserted_at, _ = self._entries[oldest_key]
                oldest_age = max(0.0, self._clock() - oldest_inserted_at)
            total = self._hits + self._misses
            hit_rate = (self._hits / total) if total > 0 else 0.0
            return EmbedCacheStats(
                size=size,
                hits=self._hits,
                misses=self._misses,
                evictions=self._evictions,
                oldest_entry_age_s=oldest_age,
                hit_rate=hit_rate,
            )

    def clear(self) -> None:
        """Drop every cached entry and reset counters.

        Used by tests between cases and by any future cache-bust event
        (e.g. embed-model version change — out of scope here).
        Truncates the SQLite layer too when persistence is wired so a
        cache-bust survives process restart.
        """
        with self._lock:
            self._entries.clear()
            self._hits = 0
            self._misses = 0
            self._evictions = 0
            if self._conn is not None:
                try:
                    with self._conn:
                        self._conn.execute(_TRUNCATE_SQL)
                except sqlite3.Error as exc:  # pragma: no cover — defensive truncate fallback.
                    logger.warning(
                        "EmbedCache: persistence truncate failed — file may still hold stale rows. cause: %s",
                        exc,
                    )

    def _is_expired(self, inserted_at: float) -> bool:
        """Internal age check — caller already holds the lock."""
        return (self._clock() - inserted_at) > self._max_age_s


# ---------------------------------------------------------------------------
# Process-shared singleton
# ---------------------------------------------------------------------------

_EMBED_CACHE: EmbedCache | None = None
_EMBED_CACHE_LOCK = threading.Lock()


def _resolve_embed_cache_path() -> Path | None:
    """Resolve the persistence path for the process-shared singleton.

    Returns ``None`` when running under pytest so test runs don't write
    embed-cache files into the developer's real data dir — mirrors the
    ``PYTEST_CURRENT_TEST`` guard used by
    :func:`kairix.core.embed._deps_defaults.default_open_embedding_cache`.
    F4-clean — the env read lives at the paths boundary.

    Path failures (no ``KAIRIX_DATA_DIR``, paths module unimportable
    under partial installs) degrade gracefully to in-memory-only with
    a logged warning.
    """
    import os

    if os.environ.get("PYTEST_CURRENT_TEST"):
        return None
    try:
        from kairix.paths import embed_cache_path

        return embed_cache_path()
    except Exception as exc:
        logger.warning(
            "EmbedCache: failed to resolve persistence path — degrading to in-memory-only. cause: %s",
            exc,
        )
        return None


def get_embed_cache() -> EmbedCache:
    """Return the process-shared :class:`EmbedCache`, building it lazily.

    Bounds are read from env vars on first construction:
      - ``KAIRIX_EMBED_CACHE_MAX_ENTRIES`` (int, default 1000)
      - ``KAIRIX_EMBED_CACHE_MAX_AGE_S`` (float seconds, default 1800)

    Persistence path resolves to :func:`kairix.paths.embed_cache_path`
    (``data_dir() / "embed_cache.sqlite"``) so the cache survives
    ``docker compose restart`` — closes #391. Test runs (detected via
    ``PYTEST_CURRENT_TEST``) get a path-less in-memory-only singleton
    so cache files don't leak into the developer's data dir.

    F4-clean: env reads route through :mod:`kairix.paths`.
    """
    global _EMBED_CACHE
    with _EMBED_CACHE_LOCK:
        if _EMBED_CACHE is None:
            from kairix.paths import read_float_env, read_int_env

            max_entries = read_int_env("KAIRIX_EMBED_CACHE_MAX_ENTRIES", default=DEFAULT_MAX_ENTRIES)
            max_age_s = read_float_env("KAIRIX_EMBED_CACHE_MAX_AGE_S", default=DEFAULT_MAX_AGE_S)
            path = _resolve_embed_cache_path()
            _EMBED_CACHE = EmbedCache(max_entries=max_entries, max_age_s=max_age_s, path=path)
        return _EMBED_CACHE


def reset_embed_cache() -> None:
    """Drop the process-shared cache instance.

    Tests use this between cases instead of monkey-patching env vars
    (F2). After ``reset_embed_cache()`` the next :func:`get_embed_cache`
    call rebuilds the cache fresh — so a test wanting a smaller bound
    can set the env var, call reset, then call get; but the
    *recommended* pattern is to construct ``EmbedCache(max_entries=N)``
    directly and skip the singleton entirely.
    """
    global _EMBED_CACHE
    with _EMBED_CACHE_LOCK:
        _EMBED_CACHE = None


def install_embed_cache(cache: EmbedCache | None) -> None:
    """Install ``cache`` as the process-shared singleton.

    Pass an :class:`EmbedCache` (or ``None`` to clear) and the next
    :func:`get_embed_cache` returns it. Tests use this to inject a
    pre-built cache with custom bounds through the public write
    accessor instead of reassigning the module attribute.
    """
    global _EMBED_CACHE
    with _EMBED_CACHE_LOCK:
        _EMBED_CACHE = cache
