"""In-process TTL cache wrapping :class:`TopologyCollectionResolver` (#388).

The MCP server's executor threads all share one SQLite Connection on the
topology collection resolver (post-#386: ``check_same_thread=False``).
SQLite serialises access via its internal lock, so under concurrent load
N threads issuing the same SELECT serialise on the lock and the resolve
stage tail latency blows out.

Scope-profile changes are minute-scale operator actions, not query-scale
events. Caching the resolver's ``(agent, scope) -> collections`` output
for a short TTL turns the hot path into a memory lookup. Cache hits
short-circuit the SQLite roundtrip entirely.

Design mirrors :class:`QueryResultCache`:

* Bounded LRU + per-entry TTL; expired entries report as misses.
* :class:`threading.RLock` guards the dict (resolver itself is called
  outside the lock so an in-flight SELECT doesn't block other readers).
* Explicit ``clear()`` so operator config-reload paths can invalidate.

The wrapper preserves the full :class:`TopologyCollectionResolver`
public surface: ``validate_explicit`` and any future methods passthrough
unchanged (only ``resolve`` is cached because it's the search hot path).
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from kairix.core.search.topology_resolver import TopologyCollectionResolver

DEFAULT_MAX_ENTRIES = 256
DEFAULT_MAX_AGE_S = 600.0  # 10 minutes — scope-profile changes are minute-scale


class ScopeCollectionCache:
    """Wraps a :class:`TopologyCollectionResolver` with a TTL-bounded LRU.

    Cache key is ``(agent_or_None, scope_string)``. Value is the
    resolver's ``list[str] | None`` output reused as-is.

    Operator-facing consistency model: a new entry in
    ``topology_scope_profiles`` or ``topology_scope_entries`` takes up
    to ``max_age_s`` (default 10 minutes) to surface in search. The
    worker calls :meth:`clear` whenever it reloads the config (mirrors
    the existing ``reset_query_result_cache`` pattern), so a deliberate
    operator restart picks up the change immediately.
    """

    def __init__(
        self,
        inner: TopologyCollectionResolver,
        *,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        max_age_s: float = DEFAULT_MAX_AGE_S,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._inner = inner
        self._max_entries = max(1, int(max_entries))
        self._max_age_s = float(max_age_s)
        self._entries: OrderedDict[tuple[str | None, str], tuple[float, list[str] | None]] = OrderedDict()
        self._lock = threading.RLock()
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._clock = clock

    def resolve(self, agent: str | None, scope: object) -> list[str] | None:
        """Cached delegate of :meth:`TopologyCollectionResolver.resolve`."""
        key = (agent, str(scope))
        now = self._clock()
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                inserted_at, value = entry
                if now - inserted_at < self._max_age_s:
                    self._entries.move_to_end(key)
                    self._hits += 1
                    return value
                # Expired — drop and fall through to miss path.
                del self._entries[key]
        # Issue the SELECT outside the lock so an in-flight resolver
        # call doesn't block cache reads on other (agent, scope) pairs.
        result = self._inner.resolve(agent, scope)
        with self._lock:
            self._entries[key] = (now, result)
            self._entries.move_to_end(key)
            self._misses += 1
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
                self._evictions += 1
        return result

    def validate_explicit(self, *args: Any, **kwargs: Any) -> Any:
        """Passthrough to the inner resolver — explicit-collections path is uncached.

        ``validate_explicit`` is called from a different code path
        (operator-specified ``collections=[...]``) and isn't a hot path
        the same way ``resolve`` is. Mirror the inner resolver's
        behaviour 1:1.
        """
        return self._inner.validate_explicit(*args, **kwargs)

    def clear(self) -> None:
        """Drop every cached entry. Operator config-reload paths call this."""
        with self._lock:
            self._entries.clear()

    def stats(self) -> dict[str, int]:
        """Read-only snapshot for the onboard envelope / probe logs."""
        with self._lock:
            return {
                "size": len(self._entries),
                "hits": self._hits,
                "misses": self._misses,
                "evictions": self._evictions,
            }
