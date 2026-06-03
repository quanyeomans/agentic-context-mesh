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
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

DEFAULT_MAX_ENTRIES = 256
DEFAULT_MAX_AGE_S = 300.0  # 5 minutes — prep summaries are session-scale


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
    ) -> None:
        self._max_entries = max(1, int(max_entries))
        self._max_age_s = float(max_age_s)
        self._entries: OrderedDict[tuple[str, str, str], tuple[float, str]] = OrderedDict()
        self._lock = threading.RLock()
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._clock = clock

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
                return
            self._entries[key] = (now, value)
            if len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
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
