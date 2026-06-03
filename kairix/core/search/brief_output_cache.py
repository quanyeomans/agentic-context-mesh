"""In-process cache for ``BriefOutput`` instances (#396 W-B Commit 4).

``run_brief`` runs a 6-source fan-out + LLM synthesis to produce a
session briefing. Each invocation costs hundreds of milliseconds even
post-Workstream-B cache layer; repeating the same brief within
seconds (e.g. an agent re-fetching after a short pause) shouldn't
re-pay that cost.

This cache turns repeat ``(agent, budget)`` calls into in-memory
lookups. Cache hits return the prior :class:`BriefOutput` (or its
serialised equivalent) directly.

TTL is deliberately short (30s default): briefing context is meant
to be near-live, so caching beyond a half-minute window risks
returning stale "pending" / "blocked" items to the agent. Cache hits
are a session-scale optimisation, not a session-spanning one.

Design mirrors the other TTL-LRU caches in ``kairix.core.search``:
bounded LRU + per-entry TTL + ``threading.RLock`` + ``stats()`` + ``clear()``.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

DEFAULT_MAX_ENTRIES = 32
DEFAULT_MAX_AGE_S = 30.0  # 30 seconds — briefs are meant to be near-live


@dataclass(frozen=True)
class BriefOutputCacheStats:
    """Read-only snapshot of cache state for the probe-caches CLI."""

    size: int
    hits: int
    misses: int
    evictions: int
    hit_rate: float  # 0.0 to 1.0


class BriefOutputCache:
    """Bounded LRU cache for :class:`BriefOutput` instances.

    Key shape: ``(agent: str, budget: int)``. Value: the most recent
    :class:`BriefOutput` returned by the generate fn for that key. Age
    is checked at get-time so expired entries report as misses.

    Thread safety: one :class:`threading.RLock` guards reads + writes.
    The lock window is tight (dict ops only) so concurrent MCP worker
    threads serving brief requests don't serialise on it.
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
        self._entries: OrderedDict[tuple[str, int], tuple[float, Any]] = OrderedDict()
        self._lock = threading.RLock()
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._clock = clock

    def get(self, key: tuple[str, int]) -> Any | None:
        """Return the cached BriefOutput or ``None``. Expired entries miss."""
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

    def put(self, key: tuple[str, int], value: Any) -> None:
        """Insert or refresh an entry. Evicts the oldest when bounded."""
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

    def stats(self) -> BriefOutputCacheStats:
        """Return an atomic snapshot of cache state."""
        with self._lock:
            total = self._hits + self._misses
            hit_rate = (self._hits / total) if total > 0 else 0.0
            return BriefOutputCacheStats(
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


def make_brief_cache_key(agent: str, budget: int) -> tuple[str, int]:
    """Build the canonical ``(agent, budget)`` cache key.

    Budget is currently always the default in production callers, but
    keying on it leaves room for callers that ask for tighter / looser
    briefings without collapsing into the default slot.
    """
    return (agent, int(budget))
