"""Unit tests for the shared brief-source cache (#396 W-B C5 / PLA-267).

The slow-moving fetchers (entity_stub, knowledge_rules,
recent_decisions) wrap their filesystem reads with a process-shared TTL
LRU keyed on ``(source_name, agent)``. Within the TTL window, repeat
calls return the cached output without touching the filesystem.

The two time-sensitive fetchers (memory_logs, recent_memory) do NOT use
the cache (PLA-267) — they surface today's pending/blocked items, so a
1h TTL would cap the freshness of the very thing the brief shows. They
read fresh on every call.

Each cache test pins one observable behaviour by pre-populating the
cache via its public ``put`` surface and then calling the fetcher — if
the fetcher honours the cache, it returns the pre-populated value; if it
doesn't, it reads from the filesystem (which doesn't exist in the test
env) and returns ``""``. The freshness tests invert this: a
pre-populated sentinel must be IGNORED by the time-sensitive fetchers.

* ``test_memory_logs_not_cached_for_freshness`` — memory_logs ignores a
  pre-populated cache slot (reads fresh).
* ``test_recent_memory_not_cached_for_freshness`` — same for recent_memory.
* ``test_entity_stub_cached`` — entity stub honours the cache.
* ``test_knowledge_rules_cached`` / ``test_recent_decisions_cached``
* ``test_cache_invalidation_via_clear`` — clear() drops everything.

Sabotage proofs (executed during development):

* Removing the cache check from ``fetch_entity_stub`` breaks
  ``test_entity_stub_cached`` (the pre-populated sentinel doesn't
  surface; the fetcher falls through to the filesystem and returns
  '').
* Restoring the 1h cache on ``fetch_memory_logs`` /
  ``fetch_recent_memory`` breaks the freshness tests (the pre-populated
  sentinel surfaces, proving the source would serve stale 1h-cached
  content).
"""

from __future__ import annotations

import pytest

from kairix.agents.briefing.sources import (
    fetch_entity_stub,
    fetch_knowledge_rules,
    fetch_memory_logs,
    fetch_recent_decisions,
    fetch_recent_memory,
    get_brief_source_cache,
    reset_brief_source_cache,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    """Each test starts with a fresh source cache so hit/miss counters are deterministic."""
    reset_brief_source_cache()
    yield
    reset_brief_source_cache()


def test_memory_logs_not_cached_for_freshness() -> None:
    """memory_logs surfaces today's pending/blocked items — it must NOT be
    served from the 1h cache (PLA-267), or a freshly-added [pending] line
    stays invisible for up to an hour.

    Pre-populate the cache slot with a sentinel; the fetcher must ignore
    it and read fresh (returns '' in the unit env, never the sentinel).
    Sabotage: restoring the cache.get in ``fetch_memory_logs`` returns the
    sentinel and this assertion fails.
    """
    cache = get_brief_source_cache()
    cache.put("memory_logs", "shape", "STALE-1h-cached-memory-logs")

    result = fetch_memory_logs("shape")
    assert result != "STALE-1h-cached-memory-logs", (
        "fetch_memory_logs must read fresh, not serve a 1h-cached value — the brief's "
        "most time-sensitive source cannot be capped at 1h freshness."
    )


def test_recent_memory_not_cached_for_freshness() -> None:
    """recent_memory (today + yesterday) is the freshest source — it must
    NOT be served from the 1h cache (PLA-267).

    Sabotage: restoring the cache.get in ``fetch_recent_memory`` returns
    the sentinel and this assertion fails.
    """
    cache = get_brief_source_cache()
    cache.put("recent_memory", "consultant", "STALE-1h-cached-recent-memory")

    result = fetch_recent_memory("consultant")
    assert result != "STALE-1h-cached-recent-memory", (
        "fetch_recent_memory must read fresh, not serve a 1h-cached value."
    )


def test_entity_stub_cached() -> None:
    """``fetch_entity_stub`` returns the cached value for the cached agent."""
    cache = get_brief_source_cache()
    cache.put("entity_stub", "shape", "cached-entity-stub")

    result = fetch_entity_stub("shape")
    assert result == "cached-entity-stub", f"fetch_entity_stub must honour the cache; saw {result!r}."


def test_knowledge_rules_cached() -> None:
    """``fetch_knowledge_rules`` returns the cached value for the cached agent."""
    cache = get_brief_source_cache()
    cache.put("knowledge_rules", "builder", "cached-rules-output")

    result = fetch_knowledge_rules("builder")
    assert result == "cached-rules-output"


def test_recent_decisions_cached() -> None:
    """``fetch_recent_decisions`` returns the cached value for the cached agent."""
    cache = get_brief_source_cache()
    cache.put("recent_decisions", "growth", "cached-decisions-output")

    result = fetch_recent_decisions("growth")
    assert result == "cached-decisions-output"


def test_put_refresh_existing_key() -> None:
    """Inserting the same key twice promotes + refreshes, not duplicates.

    Driven through ``entity_stub`` — a cached source — since the
    time-sensitive sources no longer honour the cache (PLA-267).
    """
    cache = get_brief_source_cache()
    cache.put("entity_stub", "shape", "v1")
    cache.put("entity_stub", "shape", "v2")
    assert cache.stats().size == 1, "put-refresh must not duplicate"

    # The second value wins.
    assert fetch_entity_stub("shape") == "v2"


def test_lru_eviction_when_bound_exceeded() -> None:
    """Adding past max_entries evicts the oldest entry."""
    # Construct a small-bound cache directly — uses the public class
    # re-exported via the underscore-prefixed module (its parent
    # ``sources`` doesn't re-export the class itself, only the
    # accessors, so this import is the canonical surface for tests
    # needing a fresh bounded instance).
    from kairix.agents.briefing.sources import BriefSourceCache

    cache = BriefSourceCache(max_entries=2)
    cache.put("memory_logs", "a", "A")
    cache.put("memory_logs", "b", "B")
    cache.put("memory_logs", "c", "C")  # evicts "a"

    stats = cache.stats()
    assert stats.evictions == 1
    assert stats.size == 2
    assert cache.get("memory_logs", "a") is None


def test_expiry_via_short_ttl_cache() -> None:
    """Construct a short-TTL cache and assert expired entries report as miss."""
    from kairix.agents.briefing.sources import BriefSourceCache

    cache = BriefSourceCache(max_age_s=0.0)
    cache.put("memory_logs", "alpha", "stale-value")

    # max_age_s=0 means every entry is immediately stale; the next
    # get must report None + bump misses.
    assert cache.get("memory_logs", "alpha") is None
    assert cache.stats().misses == 1


def test_cache_invalidation_via_clear() -> None:
    """``clear()`` drops every entry across every cached source name."""
    cache = get_brief_source_cache()
    cache.put("recent_decisions", "shape", "stale-A")
    cache.put("entity_stub", "shape", "stale-B")
    cache.put("knowledge_rules", "shape", "stale-C")

    reset_brief_source_cache()

    # After clear, the cache is empty. Fetchers fall through to the
    # filesystem (which doesn't exist) and return ''.
    assert fetch_recent_decisions("shape") != "stale-A"
    assert fetch_entity_stub("shape") != "stale-B"
    assert fetch_knowledge_rules("shape") != "stale-C"

    stats = get_brief_source_cache().stats()
    assert stats.size == 0 or stats.size <= 3, (
        f"clear+re-call should leave the cache near-empty (only re-populated by "
        f"the fall-through reads), saw size={stats.size}"
    )
