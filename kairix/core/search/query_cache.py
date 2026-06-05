"""In-process query-result cache for the search pipeline (#281).

LRU bounded by entry count + per-entry max age. Thread-safe (kairix MCP
serves multiple agents concurrently). Cache key is the normalised
``(query, scope, agent, collections)`` four-tuple — the same query under
the same constraints returns the same result.

Cache hits sidestep the entire pipeline including the dominant Azure
embed HTTP cost (~240 ms on cache miss vs sub-millisecond on hit). In
teaming sessions where multiple agents ask near-duplicate questions
within the cache window, this is the highest-leverage Tier 1 lever
identified in :doc:`docs/architecture/teaming-concurrency-strategy.md`.

Design notes:

- ``OrderedDict`` backs the LRU. ``move_to_end(key)`` promotes on
  access; ``popitem(last=False)`` evicts the oldest entry when the
  bound is exceeded.
- Each entry stores ``(insertion_time_s, value)``. ``get`` checks age
  at read time so a stale-but-not-yet-evicted entry is reported as a
  miss (and the operator-facing hit/miss stats stay honest).
- A single :class:`threading.RLock` guards all reads + writes. The
  cost of contention here is dwarfed by the cost of the Azure embed
  roundtrip the cache avoids on a hit.
- Invalidation is process-restart-only for now. A future ticket may
  add cache-bust on embed/store-crawl mutation events; that is out of
  scope for #281.

Persistence (#411 Phase 2)
--------------------------

Construct with ``path=...`` + ``cfg_hash=...`` to back the in-memory LRU
with a SQLite file on disk. ``put`` is write-through (INSERT OR REPLACE)
so the next process restart finds the entries already populated;
``__init__`` replays on-disk rows whose ``cfg_hash`` column matches the
current ``cfg_hash`` argument into the in-memory ``OrderedDict``. Entries
written under a different cfg_hash are ignored on replay — cfg changes
(provider swap, fusion-strategy change, etc.) invalidate the persisted
cache automatically.

Construction with ``path=None`` keeps the original in-memory-only
behaviour for tests and ad-hoc instances. Persistence + ``cfg_hash`` is
opt-in via the factory wiring (see ``kairix.core.factory``); direct
``QueryResultCache(...)`` callers default to in-memory-only.

On-disk values are stored as JSON (via :func:`dataclasses.asdict`) —
no binary blob formats, no arbitrary-code-on-load risk, schema drift
handled by the ``schema_version`` row in the meta table.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import asdict, dataclass, fields, is_dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MAX_ENTRIES = 500
DEFAULT_MAX_AGE_S = 300.0  # 5 minutes — in-memory; on-disk uses DEFAULT_DISK_MAX_AGE_S
DEFAULT_DISK_MAX_AGE_S = 3600.0  # 1 hour — cold CLI starts (#411 Phase 2) tolerate older entries.

# Schema version. Bumped when the on-disk shape changes incompatibly so a
# rolled-out client sees an unfamiliar version and drops + recreates the
# table rather than reading bad rows.
_SCHEMA_VERSION = "1"

_TABLE = "query_cache"
_META_TABLE = "query_cache_meta"
_CREATE_META_SQL = f"CREATE TABLE IF NOT EXISTS {_META_TABLE} (  key   TEXT PRIMARY KEY,  value TEXT NOT NULL)"
_CREATE_SQL = (
    f"CREATE TABLE IF NOT EXISTS {_TABLE} ("
    "  cfg_hash    TEXT NOT NULL,"
    "  key_hash    TEXT NOT NULL,"
    "  inserted_at REAL NOT NULL,"
    "  expires_at  REAL NOT NULL,"
    "  payload     TEXT NOT NULL,"
    "  PRIMARY KEY (cfg_hash, key_hash)"
    ")"
)
_UPSERT_SQL = (
    f"INSERT OR REPLACE INTO {_TABLE} (cfg_hash, key_hash, inserted_at, expires_at, payload) VALUES (?, ?, ?, ?, ?)"
)
_SELECT_BY_CFG_SQL = (
    f"SELECT key_hash, inserted_at, expires_at, payload FROM {_TABLE} "
    "WHERE cfg_hash = ? ORDER BY inserted_at ASC LIMIT ?"
)
_DELETE_KEY_SQL = f"DELETE FROM {_TABLE} WHERE cfg_hash = ? AND key_hash = ?"
_TRUNCATE_SQL = f"DELETE FROM {_TABLE}"
_META_GET_SQL = f"SELECT value FROM {_META_TABLE} WHERE key = ?"
_META_UPSERT_SQL = f"INSERT OR REPLACE INTO {_META_TABLE} (key, value) VALUES (?, ?)"


def _key_hash(key: tuple[Any, ...]) -> str:
    """Stable SHA-256 hash of an in-memory cache key.

    The in-memory key is a tuple of (normalised_query, scope, agent,
    collections_tuple). We need a deterministic short string to use as
    the on-disk primary-key column.
    """
    # Use repr to capture tuple shape + nested tuples; cheap, stable.
    return hashlib.sha256(repr(key).encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class CacheStats:
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


class QueryResultCache:
    """Bounded LRU cache for SearchPipeline results.

    Key shape: tuple of ``(query_normalised, scope, agent, collections_tuple)``.
    Value: the :class:`SearchResult` instance returned by
    :meth:`SearchPipeline.search`. Age is checked at get-time — expired
    entries are removed and treated as misses (so stats reflect
    operator-facing reality, not raw-LRU shape).

    Thread safety: a single :class:`threading.RLock` guards all reads +
    writes. The cost of contention at this lock is negligible vs the
    cost of an Azure embed roundtrip we're avoiding on every hit.
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
        self._entries: OrderedDict[tuple[Any, ...], tuple[float, Any]] = OrderedDict()
        self._lock = threading.RLock()
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        # Public DI seam — tests pass a controllable clock to drive
        # expiry without monkey-patching ``time.time`` inside the module.
        self._clock = clock
        # On-disk persistence (#411 Phase 2). ``path`` opt-in; ``cfg_hash``
        # scopes rows to the pipeline-build configuration that produced
        # them — rows under a different cfg_hash never replay.
        self._path: Path | None = Path(path) if path is not None else None
        self._cfg_hash = str(cfg_hash)
        self._disk_max_age_s = float(disk_max_age_s)
        self._conn: sqlite3.Connection | None = None
        if self._path is not None:
            self._open_and_replay()

    def _open_and_replay(self) -> None:
        """Open the SQLite file (creating it if needed) and replay rows into the LRU.

        Mirrors :meth:`kairix.transport.cache.EmbedCache._open_and_replay`
        — same defensive pattern: parent mkdir, schema-version check
        (drop + recreate on mismatch), bounded replay, defensive
        exception handling so disk failures degrade to in-memory-only
        rather than crashing the search path.
        """
        assert self._path is not None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # query-result persistence DB (#411 Phase 2). Same trust boundary as embed_cache
            # F77-allow: kairix-user-owned data dir; not a writer-coordinator concern.
            conn = sqlite3.connect(str(self._path), check_same_thread=False)
            conn.execute(_CREATE_META_SQL)
            conn.execute(_CREATE_SQL)
            conn.commit()
            self._conn = conn
        except (OSError, sqlite3.Error) as exc:
            logger.warning(
                "QueryResultCache: failed to open persistence file %s — degrading to in-memory-only. cause: %s",
                self._path,
                exc,
            )
            self._conn = None
            return

        try:
            self._check_schema_version_locked()
        except sqlite3.Error as exc:  # pragma: no cover — defensive
            logger.warning(
                "QueryResultCache: schema-version probe failed for %s — degrading to in-memory-only. cause: %s",
                self._path,
                exc,
            )
            self._conn = None
            return

        try:
            self._replay_matching_cfg_rows()
        except sqlite3.Error as exc:  # pragma: no cover — defensive replay
            logger.warning(
                "QueryResultCache: failed to replay rows from %s — starting empty. cause: %s",
                self._path,
                exc,
            )

    def _replay_matching_cfg_rows(self) -> None:
        """Replay only rows matching ``self._cfg_hash`` into the in-memory LRU.

        Extracted from :meth:`_open_and_replay` to keep cognitive
        complexity bounded. A different cfg_hash on disk means a
        different pipeline shape from the previous process (provider
        swap, fusion-strategy change, etc.); those rows are dropped on
        sight rather than served.
        """
        assert self._conn is not None
        # F63-bounded: LIMIT capped at self._max_entries (replay never
        # loads more rows than the in-memory LRU can hold).
        cursor = self._conn.execute(_SELECT_BY_CFG_SQL, (self._cfg_hash, self._max_entries))
        now = self._clock()
        for key_hash_str, inserted_at, expires_at, payload in cursor.fetchall():
            if expires_at <= now:
                self._conn.execute(_DELETE_KEY_SQL, (self._cfg_hash, key_hash_str))
                continue
            value = self._decode_or_drop(key_hash_str, payload)
            if value is None:
                continue
            self._entries[(_REHYDRATED_MARKER, key_hash_str)] = (inserted_at, value)
            if len(self._entries) > self._max_entries:
                evicted_key, _ = self._entries.popitem(last=False)
                self._delete_persisted_for_in_memory_key(evicted_key)
                self._evictions += 1
        self._conn.commit()

    def _decode_or_drop(self, key_hash_str: str, payload: str) -> Any:
        """Decode ``payload``; on failure drop the row and return ``None``.

        Extracted so :meth:`_replay_matching_cfg_rows` stays flat. A
        row that can't be decoded (forward-compat shape drift, corrupt
        file) is dropped silently — replay stays partial-success.
        """
        assert self._conn is not None
        try:
            return _decode_search_result(payload)
        except (ValueError, KeyError, TypeError) as decode_exc:
            logger.debug(
                "QueryResultCache: dropped unreadable row key_hash=%s cause=%s",
                key_hash_str,
                decode_exc,
            )
            self._conn.execute(_DELETE_KEY_SQL, (self._cfg_hash, key_hash_str))
            return None

    def _check_schema_version_locked(self) -> None:
        """If the on-disk schema version differs, drop + recreate the table.

        Tolerates schema drift across upgrades — operator never has to
        manually delete the cache file after a kairix version bump.
        """
        if self._conn is None:
            return
        cursor = self._conn.execute(_META_GET_SQL, ("schema_version",))
        row = cursor.fetchone()
        stored = row[0] if row else None
        if stored == _SCHEMA_VERSION:
            return
        # Mismatch (or never written) — drop + recreate.
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

    def get(self, key: tuple[Any, ...]) -> Any | None:
        """Return the cached value or ``None``. Expired entries miss.

        Promotes the entry to most-recently-used on a successful hit
        so the LRU ordering reflects access, not insertion.
        """
        with self._lock:
            # First try the in-memory key directly.
            entry = self._entries.get(key)
            rehydrated_key: tuple[Any, ...] | None = None
            if entry is None:
                # Then try the rehydrated-marker key (on-disk replay
                # produces these because we don't keep the original
                # tuple, only its hash).
                kh = _key_hash(key)
                rehydrated_key = (_REHYDRATED_MARKER, kh)
                entry = self._entries.get(rehydrated_key)
                if entry is None:
                    self._misses += 1
                    return None
            inserted_at, value = entry
            if self._is_expired(inserted_at):
                # Drop the expired entry on the floor and report a miss.
                effective_key = rehydrated_key if rehydrated_key is not None else key
                del self._entries[effective_key]
                self._delete_persisted_for_in_memory_key(effective_key)
                self._misses += 1
                return None
            effective_key = rehydrated_key if rehydrated_key is not None else key
            self._entries.move_to_end(effective_key)
            self._hits += 1
            return value

    def put(self, key: tuple[Any, ...], value: Any) -> None:
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
                self._delete_persisted_for_in_memory_key(evicted_key)
                self._evictions += 1

    def stats(self) -> CacheStats:
        """Return an atomic snapshot of cache state."""
        with self._lock:
            size = len(self._entries)
            oldest_age = 0.0
            if size > 0:
                oldest_key = next(iter(self._entries))
                oldest_inserted_at, _ = self._entries[oldest_key]
                oldest_age = max(0.0, self._clock() - oldest_inserted_at)
            total = self._hits + self._misses
            hit_rate = (self._hits / total) if total > 0 else 0.0
            return CacheStats(
                size=size,
                hits=self._hits,
                misses=self._misses,
                evictions=self._evictions,
                oldest_entry_age_s=oldest_age,
                hit_rate=hit_rate,
            )

    def clear(self) -> None:
        """Drop every cached entry and reset counters.

        Used by tests between cases and by any future cache-bust event.
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
                except sqlite3.Error as exc:  # pragma: no cover — defensive
                    logger.warning(
                        "QueryResultCache: persistence truncate failed — file may still hold stale rows. cause: %s",
                        exc,
                    )

    def _upsert_persisted(self, key: tuple[Any, ...], inserted_at: float, value: Any) -> None:
        """Write a single entry to the SQLite layer. Caller holds the lock."""
        if self._conn is None:
            return
        try:
            payload = _encode_search_result(value)
        except (TypeError, ValueError) as exc:
            # Non-serializable value (custom dataclass we don't recognise,
            # or a forward-incompatible field shape). Skip the on-disk
            # write — the in-memory entry still works for this process.
            logger.debug(
                "QueryResultCache: refusing to persist non-serializable value. cause: %s",
                exc,
            )
            return
        kh = key[1] if key and key[0] is _REHYDRATED_MARKER else _key_hash(key)
        expires_at = inserted_at + self._disk_max_age_s
        try:
            with self._conn:
                self._conn.execute(_UPSERT_SQL, (self._cfg_hash, kh, inserted_at, expires_at, payload))
        except sqlite3.Error as exc:  # pragma: no cover — defensive
            logger.warning(
                "QueryResultCache: persistence write failed for key_hash %s — kept in-memory only. cause: %s",
                kh,
                exc,
            )

    def _delete_persisted_for_in_memory_key(self, key: tuple[Any, ...]) -> None:
        """Remove a single entry from the SQLite layer. Caller holds the lock."""
        if self._conn is None:
            return
        kh = key[1] if key and key[0] is _REHYDRATED_MARKER else _key_hash(key)
        try:
            with self._conn:
                self._conn.execute(_DELETE_KEY_SQL, (self._cfg_hash, kh))
        except sqlite3.Error as exc:  # pragma: no cover — defensive
            logger.warning(
                "QueryResultCache: persistence delete failed for key_hash %s. cause: %s",
                kh,
                exc,
            )

    def _is_expired(self, inserted_at: float) -> bool:
        """Internal age check — caller already holds the lock."""
        return (self._clock() - inserted_at) > self._max_age_s


# Sentinel used as the first element of the in-memory key tuple when the
# row was rehydrated from disk and we only have the on-disk key_hash
# (not the original normalised-query tuple). Two callers asking the same
# question after a cold-start will hash to the same key_hash so the
# rehydrated entry serves them via the (_REHYDRATED_MARKER, kh) shape.
class _RehydratedMarker:
    """Marker class for rehydrated-on-disk in-memory keys."""

    __slots__ = ()


_REHYDRATED_MARKER = _RehydratedMarker()


def normalise_query(query: str) -> str:
    """Case-fold + collapse whitespace so trivially-different queries share cache slots.

    ``"  Hello   WORLD  "`` and ``"hello world"`` produce the same
    normalised form. Anything beyond casing + whitespace (synonyms,
    punctuation, paraphrases) is intentionally NOT collapsed — the
    cache pretends nothing it can't prove will return the same answer.
    """
    return " ".join(query.lower().split())


def make_cache_key(
    query: str,
    scope: Any,
    agent: str | None,
    collections: list[str] | None,
) -> tuple[Any, ...]:
    """Build the canonical 4-tuple key.

    Tuples are hashable so the LRU's ``OrderedDict`` can key on them
    directly. ``collections`` is sorted before tupling so callers that
    pass equivalent lists in different orders hit the same slot.
    ``agent=None`` and ``agent=""`` collapse to the same key — both
    mean "no agent supplied".
    """
    return (
        normalise_query(query),
        scope,
        agent or "",
        tuple(sorted(collections)) if collections else (),
    )


# ---------------------------------------------------------------------------
# JSON encode / decode for SearchResult round-tripping (#411 Phase 2)
# ---------------------------------------------------------------------------


def _encode_search_result(value: Any) -> str:
    """Encode a :class:`SearchResult` (or compatible dataclass) as JSON.

    Uses :func:`dataclasses.asdict` for the natural-shape projection;
    enum fields collapse to their ``.value`` via a custom default.
    Pure-data JSON — no binary blob formats, no arbitrary-code-load
    surface (#411 Phase 2 brief).
    """
    if not is_dataclass(value):
        raise TypeError(f"QueryResultCache persistence expects a dataclass; got {type(value).__name__}")
    return json.dumps(asdict(value), default=_json_default, sort_keys=False)


def _json_default(obj: Any) -> Any:
    """JSON fallback for enum + tuple + Path values."""
    # str-Enum subclasses (QueryIntent etc.) JSON-encode via .value.
    if hasattr(obj, "value") and not isinstance(obj, dict):
        return obj.value
    if isinstance(obj, tuple):
        return list(obj)
    if isinstance(obj, Path):
        return str(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON-serializable")


def _decode_search_result(payload: str) -> Any:
    """Decode a JSON payload back into a :class:`SearchResult`.

    Reconstruction is recursive: each nested dataclass field gets its
    own per-class hydration via :func:`_hydrate_dataclass`. Unknown
    fields are dropped (forward-compat — a kairix upgrade that adds a
    field still reads old caches without crashing).
    """
    # Lazy imports to keep the module dependency-clean (avoid pulling
    # the entire search pipeline into kairix.paths-adjacent imports).
    from kairix.core.search.budget import BudgetedResult
    from kairix.core.search.intent import QueryIntent
    from kairix.core.search.pipeline import SearchResult
    from kairix.core.search.rrf import FusedResult

    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError(f"QueryResultCache payload was not an object; got {type(data).__name__}")

    # Hydrate top-level scalar fields.
    intent_raw = data.get("intent", "SEMANTIC")
    intent = QueryIntent(intent_raw) if not isinstance(intent_raw, QueryIntent) else intent_raw

    results_raw = data.get("results", [])
    hydrated_results: list[BudgetedResult] = []
    for r in results_raw:
        if not isinstance(r, dict):
            continue
        inner_raw = r.get("result", {})
        if not isinstance(inner_raw, dict):
            continue
        inner = _hydrate_dataclass(FusedResult, inner_raw)
        hydrated_results.append(
            BudgetedResult(
                result=inner,
                tier=r.get("tier", "L2"),
                token_estimate=int(r.get("token_estimate", 0)),
                content=str(r.get("content", "")),
            )
        )

    return SearchResult(
        query=str(data.get("query", "")),
        intent=intent,
        results=hydrated_results,
        bm25_count=int(data.get("bm25_count", 0)),
        vec_count=int(data.get("vec_count", 0)),
        fact_count=int(data.get("fact_count", 0)),
        fused_count=int(data.get("fused_count", 0)),
        stage_latency_ms=dict(data.get("stage_latency_ms", {})),
        collections=list(data.get("collections", [])),
        tiers_used=list(data.get("tiers_used", [])),
        total_tokens=int(data.get("total_tokens", 0)),
        latency_ms=float(data.get("latency_ms", 0.0)),
        vec_failed=bool(data.get("vec_failed", False)),
        fallback_used=bool(data.get("fallback_used", False)),
        error=str(data.get("error", "")),
    )


def _hydrate_dataclass(cls: type, data: dict[str, Any]) -> Any:
    """Construct an instance of ``cls`` from ``data``, ignoring unknown keys.

    Forward-compat: a row written by an older kairix that lacks a
    field new-kairix expects gets the field's default; a row with
    extra fields drops them silently.
    """
    field_names = {f.name for f in fields(cls)}
    filtered = {k: v for k, v in data.items() if k in field_names}
    return cls(**filtered)
