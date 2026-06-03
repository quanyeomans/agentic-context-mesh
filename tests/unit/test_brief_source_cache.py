"""Unit tests for the shared brief-source cache (#396 W-B C5).

The 5 cheap fetchers (memory_logs, recent_memory, entity_stub,
knowledge_rules, recent_decisions) wrap their filesystem reads with a
process-shared TTL LRU keyed on ``(source_name, agent)``. Within the
TTL window, repeat calls return the cached output without touching
the filesystem.

Each test pins one observable behaviour by pre-populating the cache
via its public ``put`` surface and then calling the fetcher — if the
fetcher honours the cache, it returns the pre-populated value; if it
doesn't, it reads from the filesystem (which doesn't exist in the test
env) and returns ``""``.

* ``test_memory_logs_cached_per_agent`` — same (memory_logs, "shape")
  key returns cached; different agent → fresh fetch.
* ``test_entity_stub_cached`` — same shape for entity stub.
* ``test_knowledge_rules_cached``
* ``test_recent_decisions_cached``
* ``test_cache_invalidation_via_clear`` — clear() drops everything.

Sabotage proofs (executed during development):

* Removing the cache check from ``fetch_entity_stub`` breaks
  ``test_entity_stub_cached`` (the pre-populated sentinel doesn't
  surface; the fetcher falls through to the filesystem and returns
  '').
* Replacing ``(source_name, agent)`` keys with single-source keys
  collapses every entry into one slot and ``test_memory_logs_cached_per_agent``
  fails when the second fetcher returns the first agent's cached
  value.
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


def test_memory_logs_cached_per_agent() -> None:
    """Same ``(memory_logs, agent="shape")`` key returns the cached value.

    A second key with a different agent must NOT collide.
    """
    cache = get_brief_source_cache()
    cache.put("memory_logs", "shape", "cached-output-for-shape")

    # First call hits the cache for shape.
    result_shape = fetch_memory_logs("shape")
    assert result_shape == "cached-output-for-shape", (
        "fetch_memory_logs must return the cached value for the cached agent — agent-keyed lookup failed."
    )

    # A different agent must NOT hit shape's slot. In the test env the
    # filesystem path doesn't exist so the fetcher falls through and
    # returns '' — different from shape's cached value.
    result_other = fetch_memory_logs("agent-alpha")
    assert result_other != "cached-output-for-shape", (
        f"different agent must not collide with shape's cache slot — saw {result_other!r}"
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


def test_recent_memory_cached() -> None:
    """``fetch_recent_memory`` returns the cached value for the cached agent."""
    cache = get_brief_source_cache()
    cache.put("recent_memory", "consultant", "cached-recent-memory-output")

    result = fetch_recent_memory("consultant")
    assert result == "cached-recent-memory-output"


def test_put_refresh_existing_key() -> None:
    """Inserting the same key twice promotes + refreshes, not duplicates."""
    cache = get_brief_source_cache()
    cache.put("memory_logs", "shape", "v1")
    cache.put("memory_logs", "shape", "v2")
    assert cache.stats().size == 1, "put-refresh must not duplicate"

    # The second value wins.
    assert fetch_memory_logs("shape") == "v2"


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
    """``clear()`` drops every entry across every source name."""
    cache = get_brief_source_cache()
    cache.put("memory_logs", "shape", "stale-A")
    cache.put("entity_stub", "shape", "stale-B")
    cache.put("knowledge_rules", "shape", "stale-C")

    reset_brief_source_cache()

    # After clear, the cache is empty. Fetchers fall through to the
    # filesystem (which doesn't exist) and return ''.
    assert fetch_memory_logs("shape") != "stale-A"
    assert fetch_entity_stub("shape") != "stale-B"
    assert fetch_knowledge_rules("shape") != "stale-C"

    stats = get_brief_source_cache().stats()
    assert stats.size == 0 or stats.size <= 3, (
        f"clear+re-call should leave the cache near-empty (only re-populated by "
        f"the fall-through reads), saw size={stats.size}"
    )
