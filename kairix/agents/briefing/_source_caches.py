"""In-process cache for cheap brief source fetchers (#396 W-B C5).

The 5 cheap brief sources (memory_logs, recent_memory, entity_stub,
knowledge_rules, recent_decisions) each touch the operator's filesystem
under the agent's knowledge directory. None of the bytes they return
change at query-scale — every agent's session-scope knowledge file
moves on minute-to-hour cadence, not second-by-second.

This single shared :class:`BriefSourceCache` instance turns repeat
``(source_name, agent)`` lookups into memory hits. 1-hour TTL is the
deliberate consistency tradeoff: long enough to absorb a burst of
brief calls, short enough that an operator editing a rules file in
Obsidian sees the new content reflected within an hour.

Design mirrors the other TTL-LRU caches in :mod:`kairix.core.search`:
bounded LRU + per-entry TTL + ``threading.RLock`` + ``stats()`` +
``clear()``.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

DEFAULT_MAX_ENTRIES = 64
DEFAULT_MAX_AGE_S = 3600.0  # 1 hour — knowledge files move slowly


@dataclass(frozen=True)
class BriefSourceCacheStats:
    """Read-only snapshot of cache state for the probe-caches CLI."""

    size: int
    hits: int
    misses: int
    evictions: int
    hit_rate: float  # 0.0 to 1.0


class BriefSourceCache:
    """Bounded LRU cache shared across all cheap brief fetchers.

    Key shape: ``(source_name: str, agent: str)``. Value: the fetcher's
    output string for that pair. Age is checked at get-time so expired
    entries report as misses.

    One cache, one key shape — the 5 cheap fetchers all share this
    instance via :func:`get_brief_source_cache` so an operator can read
    aggregate hit/miss counts from a single ``stats()`` call.
    """

    def __init__(
        self,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        max_age_s: float = DEFAULT_MAX_AGE_S,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._max_entries = max(1, int(max_entries))
        self._max_age_s = float(max_age_s)
        self._entries: OrderedDict[tuple[str, str], tuple[float, str]] = OrderedDict()
        self._lock = threading.RLock()
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._clock = clock

    def get(self, source_name: str, agent: str) -> str | None:
        """Return the cached output or ``None``. Expired entries miss."""
        key = (source_name, agent)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self._misses += 1
                return None
            inserted_at, value = entry
            if (self._clock() - inserted_at) > self._max_age_s:
                del self._entries[key]
                self._misses += 1
                return None
            self._entries.move_to_end(key)
            self._hits += 1
            return value

    def put(self, source_name: str, agent: str, value: str) -> None:
        """Insert or refresh an entry. Evicts the oldest when bounded."""
        key = (source_name, agent)
        with self._lock:
            now = self._clock()
            if key in self._entries:
                self._entries[key] = (now, value)
                self._entries.move_to_end(key)
                return
            self._entries[key] = (now, value)
            if len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
                self._evictions += 1

    def stats(self) -> BriefSourceCacheStats:
        """Return an atomic snapshot of cache state."""
        with self._lock:
            total = self._hits + self._misses
            hit_rate = (self._hits / total) if total > 0 else 0.0
            return BriefSourceCacheStats(
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


# Process-shared singleton. Lazy-initialised on first call so test
# fixtures running before any brief invocation don't pay the
# construction cost.
_BRIEF_SOURCE_CACHE: BriefSourceCache | None = None
_BRIEF_SOURCE_CACHE_LOCK = threading.Lock()


def get_brief_source_cache() -> BriefSourceCache:
    """Return the process-shared :class:`BriefSourceCache`.

    Public accessor for the probe-caches CLI to surface hit / miss /
    eviction counts.
    """
    global _BRIEF_SOURCE_CACHE
    with _BRIEF_SOURCE_CACHE_LOCK:
        if _BRIEF_SOURCE_CACHE is None:
            _BRIEF_SOURCE_CACHE = BriefSourceCache()
        return _BRIEF_SOURCE_CACHE


def reset_brief_source_cache() -> None:
    """Drop every cached source output. Tests + operator reload paths call this."""
    with _BRIEF_SOURCE_CACHE_LOCK:
        if _BRIEF_SOURCE_CACHE is not None:
            _BRIEF_SOURCE_CACHE.clear()
