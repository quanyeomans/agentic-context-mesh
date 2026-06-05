"""In-process cache for prep summaries (#396 W-B Commit 3).

``run_prep`` synthesises a topic summary by calling the configured
LLM's ``chat()`` endpoint with the retrieved context. Each call costs
hundreds of milliseconds (network roundtrip + token generation) and
agents asking the same prep question within a session see no value
from re-doing the LLM work.

This cache turns repeat ``(query, tier, context)`` calls into in-memory
lookups. Cache hits skip the LLM roundtrip entirely.

Design mirrors :class:`QueryResultCache` and :class:`ScopeCollectionCache`
(the canonical TTL-LRU shape in kairix):

* Bounded LRU + per-entry TTL; expired entries report as misses.
* ``threading.RLock`` guards the dict so concurrent MCP worker threads
  serving the same query don't race-write.
* ``stats()`` returns a read-only snapshot for the probe-caches CLI.
* ``clear()`` is the operator surface for cache-bust events.

Cache key is ``(normalised_query, tier, context_hash)``. The context
hash is a sha256 digest of the retrieved-context string truncated to
32 hex chars; identical context retrievals collapse to one summary.

Persistence (#411 Phase 2)
--------------------------

Construct with ``path=...`` + ``cfg_hash=...`` to back the in-memory LRU
with a SQLite file on disk. ``put`` is write-through, ``__init__``
replays rows whose ``cfg_hash`` column matches. Default disk TTL
6 hours (prep summaries depend only on (query, tier, retrieved-
context) — context hash already invalidates when retrieval shifts; the
TTL is a backstop against stale LLM-generated text). Stored as plain
TEXT — no binary blob formats.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_MAX_ENTRIES = 256
DEFAULT_MAX_AGE_S = 300.0  # 5 minutes — prep summaries are session-scale
DEFAULT_DISK_MAX_AGE_S = 21600.0  # 6 hours — cold CLI starts (#411 Phase 2)

_SCHEMA_VERSION = "1"
_TABLE = "prep_summary_cache"
_META_TABLE = "prep_summary_cache_meta"
_CREATE_META_SQL = f"CREATE TABLE IF NOT EXISTS {_META_TABLE} (  key   TEXT PRIMARY KEY,  value TEXT NOT NULL)"
_CREATE_SQL = (
    f"CREATE TABLE IF NOT EXISTS {_TABLE} ("
    "  cfg_hash    TEXT NOT NULL,"
    "  query       TEXT NOT NULL,"
    "  tier        TEXT NOT NULL,"
    "  context_h   TEXT NOT NULL,"
    "  inserted_at REAL NOT NULL,"
    "  expires_at  REAL NOT NULL,"
    "  summary     TEXT NOT NULL,"
    "  PRIMARY KEY (cfg_hash, query, tier, context_h)"
    ")"
)
_UPSERT_SQL = (
    f"INSERT OR REPLACE INTO {_TABLE} (cfg_hash, query, tier, context_h, inserted_at, expires_at, summary) "
    "VALUES (?, ?, ?, ?, ?, ?, ?)"
)
_SELECT_BY_CFG_SQL = (
    f"SELECT query, tier, context_h, inserted_at, expires_at, summary FROM {_TABLE} "
    "WHERE cfg_hash = ? ORDER BY inserted_at ASC LIMIT ?"
)
_DELETE_KEY_SQL = f"DELETE FROM {_TABLE} WHERE cfg_hash = ? AND query = ? AND tier = ? AND context_h = ?"
_TRUNCATE_SQL = f"DELETE FROM {_TABLE}"
_META_GET_SQL = f"SELECT value FROM {_META_TABLE} WHERE key = ?"
_META_UPSERT_SQL = f"INSERT OR REPLACE INTO {_META_TABLE} (key, value) VALUES (?, ?)"


@dataclass(frozen=True)
class PrepSummaryCacheStats:
    """Read-only snapshot of cache state for the probe-caches CLI."""

    size: int
    hits: int
    misses: int
    evictions: int
    hit_rate: float  # 0.0 to 1.0


class PrepSummaryCache:
    """Bounded LRU cache for LLM-synthesised prep summaries.

    Key shape: ``(normalised_query: str, tier: str, context_hash: str)``.
    Value: the LLM ``chat()`` response string for that triple. Age is
    checked at get-time so expired entries report as misses (operator
    stats stay honest).

    Thread safety: a single :class:`threading.RLock` guards reads +
    writes. Contention cost is dwarfed by the LLM roundtrip the cache
    avoids on hits.
    """

    def __init__(
        self,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        max_age_s: float = DEFAULT_MAX_AGE_S,
        *,
        clock: Callable[[], float] = time.time,
        path: Path | str | None = None,
        cfg_hash: str = "",
        disk_max_age_s: float = DEFAULT_DISK_MAX_AGE_S,
    ) -> None:
        self._max_entries = max(1, int(max_entries))
        self._max_age_s = float(max_age_s)
        self._entries: OrderedDict[tuple[str, str, str], tuple[float, str]] = OrderedDict()
        self._lock = threading.RLock()
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._clock = clock
        # On-disk persistence (#411 Phase 2).
        self._path: Path | None = Path(path) if path is not None else None
        self._cfg_hash = str(cfg_hash)
        self._disk_max_age_s = float(disk_max_age_s)
        self._conn: sqlite3.Connection | None = None
        if self._path is not None:
            self._open_and_replay()

    def _open_and_replay(self) -> None:
        """Open the SQLite file and replay matching-cfg rows into the LRU."""
        assert self._path is not None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # prep-summary persistence DB (#411 Phase 2). Same trust boundary as embed_cache
            # F77-allow: kairix-user-owned data dir; not a writer-coordinator concern.
            conn = sqlite3.connect(str(self._path), check_same_thread=False)
            conn.execute(_CREATE_META_SQL)
            conn.execute(_CREATE_SQL)
            conn.commit()
            self._conn = conn
        except (OSError, sqlite3.Error) as exc:
            logger.warning(
                "PrepSummaryCache: failed to open persistence file %s — degrading to in-memory-only. cause: %s",
                self._path,
                exc,
            )
            self._conn = None
            return

        try:
            self._check_schema_version_locked()
        except sqlite3.Error as exc:  # pragma: no cover — defensive
            logger.warning(
                "PrepSummaryCache: schema-version probe failed for %s — degrading to in-memory-only. cause: %s",
                self._path,
                exc,
            )
            self._conn = None
            return

        try:
            # F63-bounded: LIMIT capped at self._max_entries.
            cursor = self._conn.execute(_SELECT_BY_CFG_SQL, (self._cfg_hash, self._max_entries))
            now = self._clock()
            for query, tier, context_h, inserted_at, expires_at, summary in cursor.fetchall():
                if expires_at <= now:
                    self._conn.execute(_DELETE_KEY_SQL, (self._cfg_hash, query, tier, context_h))
                    continue
                key = (str(query), str(tier), str(context_h))
                self._entries[key] = (inserted_at, str(summary))
                if len(self._entries) > self._max_entries:
                    evicted_key, _ = self._entries.popitem(last=False)
                    self._delete_persisted(evicted_key)
                    self._evictions += 1
            self._conn.commit()
        except sqlite3.Error as exc:  # pragma: no cover — defensive replay
            logger.warning(
                "PrepSummaryCache: failed to replay rows from %s — starting empty. cause: %s",
                self._path,
                exc,
            )

    def _check_schema_version_locked(self) -> None:
        """Drop + recreate the table when the on-disk schema version differs."""
        if self._conn is None:
            return
        cursor = self._conn.execute(_META_GET_SQL, ("schema_version",))
        row = cursor.fetchone()
        stored = row[0] if row else None
        if stored == _SCHEMA_VERSION:
            return
        with self._conn:
            self._conn.execute(_TRUNCATE_SQL)
            self._conn.execute(_META_UPSERT_SQL, ("schema_version", _SCHEMA_VERSION))

    @property
    def path(self) -> Path | None:
        """On-disk persistence path, or ``None`` when in-memory-only."""
        return self._path

    @property
    def cfg_hash(self) -> str:
        """The cfg_hash this cache was constructed with."""
        return self._cfg_hash

    def close(self) -> None:
        """Close the underlying SQLite connection (idempotent)."""
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                finally:
                    self._conn = None

    def get(self, key: tuple[str, str, str]) -> str | None:
        """Return the cached summary or ``None``. Expired entries miss.

        Promotes the entry to MRU on a successful hit so the LRU
        ordering reflects access, not insertion.
        """
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self._misses += 1
                return None
            inserted_at, value = entry
            if (self._clock() - inserted_at) > self._max_age_s:
                # Expired — drop and report a miss.
                del self._entries[key]
                self._delete_persisted(key)
                self._misses += 1
                return None
            self._entries.move_to_end(key)
            self._hits += 1
            return value

    def put(self, key: tuple[str, str, str], value: str) -> None:
        """Insert or refresh an entry. Evicts the oldest when bounded."""
        with self._lock:
            now = self._clock()
            if key in self._entries:
                self._entries[key] = (now, value)
                self._entries.move_to_end(key)
                self._upsert_persisted(key, now, value)
                return
            self._entries[key] = (now, value)
            self._upsert_persisted(key, now, value)
            if len(self._entries) > self._max_entries:
                evicted_key, _ = self._entries.popitem(last=False)
                self._delete_persisted(evicted_key)
                self._evictions += 1

    def stats(self) -> PrepSummaryCacheStats:
        """Return an atomic snapshot of cache state."""
        with self._lock:
            total = self._hits + self._misses
            hit_rate = (self._hits / total) if total > 0 else 0.0
            return PrepSummaryCacheStats(
                size=len(self._entries),
                hits=self._hits,
                misses=self._misses,
                evictions=self._evictions,
                hit_rate=hit_rate,
            )

    def clear(self) -> None:
        """Drop every cached entry and reset counters."""
        with self._lock:
            self._entries.clear()
            self._hits = 0
            self._misses = 0
            self._evictions = 0
            if self._conn is not None:
                try:
                    with self._conn:
                        self._conn.execute(_TRUNCATE_SQL)
                except sqlite3.Error as exc:  # pragma: no cover — defensive
                    logger.warning(
                        "PrepSummaryCache: persistence truncate failed — file may still hold stale rows. cause: %s",
                        exc,
                    )

    def _upsert_persisted(self, key: tuple[str, str, str], inserted_at: float, value: str) -> None:
        """Write a single entry to the SQLite layer. Caller holds the lock."""
        if self._conn is None:
            return
        query, tier, context_h = key
        expires_at = inserted_at + self._disk_max_age_s
        try:
            with self._conn:
                self._conn.execute(
                    _UPSERT_SQL,
                    (self._cfg_hash, query, tier, context_h, inserted_at, expires_at, value),
                )
        except sqlite3.Error as exc:  # pragma: no cover — defensive
            logger.warning(
                "PrepSummaryCache: persistence write failed for key %r — kept in-memory only. cause: %s",
                key,
                exc,
            )

    def _delete_persisted(self, key: tuple[str, str, str]) -> None:
        """Remove a single entry from the SQLite layer. Caller holds the lock."""
        if self._conn is None:
            return
        query, tier, context_h = key
        try:
            with self._conn:
                self._conn.execute(_DELETE_KEY_SQL, (self._cfg_hash, query, tier, context_h))
        except sqlite3.Error as exc:  # pragma: no cover — defensive
            logger.warning(
                "PrepSummaryCache: persistence delete failed for key %r. cause: %s",
                key,
                exc,
            )


def normalise_prep_query(query: str) -> str:
    """Case-fold + collapse whitespace so trivially-different prep queries collapse.

    Mirrors :func:`kairix.core.search.query_cache.normalise_query` —
    only casing + whitespace is collapsed; synonyms / punctuation /
    paraphrases are intentionally NOT collapsed.
    """
    return " ".join(query.lower().split())


def make_prep_cache_key(query: str, tier: str, context: str) -> tuple[str, str, str]:
    """Build the canonical 3-tuple key.

    The context string is sha256-digested + truncated to 32 hex chars
    so two callers landing on the same retrieved-context block hit one
    cache slot. Truncation keeps the key small without sacrificing
    collision-resistance at the cache's bounded scale.
    """
    context_hash = hashlib.sha256(context.encode("utf-8")).hexdigest()[:32]
    return (normalise_prep_query(query), tier, context_hash)
