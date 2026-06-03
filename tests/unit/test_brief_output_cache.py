"""Unit tests for the brief-output cache wired into ``run_brief`` (#396 W-B C4).

The cache wraps the generate-fn call at the end of ``run_brief``.
Identical ``(agent, budget)`` calls within the TTL window short-circuit
the generate fn entirely; the cache returns the previously-produced
:class:`BriefOutput`.

Each test pins one observable behaviour:

* ``test_hit_short_circuits_generate`` — second call with same key
  never invokes generate_fn.
* ``test_miss_calls_generate_and_caches`` — first call delegates and
  the result is stored.
* ``test_expiry_drops_entry`` — entry past TTL → re-delegate.
* ``test_lru_eviction`` — exceed max_entries → oldest evicted.
* ``test_clear_invalidates_all`` — explicit clear() → next call
  delegates.
* ``test_different_agent_different_key`` — same budget, different
  agent → distinct cache entries.

Sabotage proofs (executed during development):

* Removing the cache wrapper around the generate_fn call in
  ``run_brief`` breaks ``test_hit_short_circuits_generate`` (generate
  call count jumps from 1 to 2).
* Removing ``agent`` from the cache key tuple makes
  ``test_different_agent_different_key`` fail.

Tests drive brief through its public entry point (:func:`run_brief`)
with a :class:`BriefDeps` carrying a recording generate fn; F5-clean.
"""

from __future__ import annotations

from typing import Any

import pytest

from kairix.core.health import HealthDeps, KairixHealth
from kairix.use_cases.brief import (
    BriefDeps,
    get_brief_output_cache,
    reset_brief_output_cache,
    run_brief,
)

pytestmark = pytest.mark.unit


class _RecordingGenerate:
    """Records every call and returns a fixed briefing string."""

    def __init__(self, body: str = "# Briefing\n\nHello.") -> None:
        self._body = body
        self.calls: list[tuple[Any, ...]] = []

    def __call__(self, agent: str, **kwargs: Any) -> str:
        self.calls.append((agent, kwargs))
        return self._body


def _healthy_deps(generate: _RecordingGenerate) -> BriefDeps:
    """Wire a BriefDeps with stubbed health probes so chat == ok.

    Without this the brief returns early without calling generate_fn —
    every test would see zero generate calls regardless of cache state.
    """
    health = HealthDeps(
        secrets_loaded_fn=lambda: True,
        embed_backend_available_fn=lambda: True,
        bm25_index_available_fn=lambda: True,
        neo4j_available_fn=lambda: True,
    )
    return BriefDeps(
        generate_fn=generate,
        briefing_dir_fn=lambda: None,
        health_deps=health,
    )


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    """Each test starts with a fresh cache so hit/miss counters are deterministic."""
    reset_brief_output_cache()
    yield
    reset_brief_output_cache()


def test_hit_short_circuits_generate() -> None:
    generate = _RecordingGenerate()
    deps = _healthy_deps(generate)

    first = run_brief("builder", deps=deps)
    second = run_brief("builder", deps=deps)

    assert isinstance(first, type(second))
    assert len(generate.calls) == 1, (
        f"expected the second run_brief call to hit the cache and skip generate_fn; "
        f"saw {len(generate.calls)} generate invocations."
    )
    stats = get_brief_output_cache().stats()
    assert stats.hits == 1
    assert stats.misses == 1


def test_miss_calls_generate_and_caches() -> None:
    generate = _RecordingGenerate()
    deps = _healthy_deps(generate)

    result = run_brief("shape", deps=deps)
    assert len(generate.calls) == 1, "cold cache must delegate to generate_fn"
    assert result.content.startswith("# Briefing")
    stats = get_brief_output_cache().stats()
    assert stats.size == 1
    assert stats.misses == 1
    assert stats.hits == 0


def test_expiry_drops_entry() -> None:
    """An entry past the TTL must be reported as a miss."""
    from kairix.core.search.brief_output_cache import BriefOutputCache

    short_lived = BriefOutputCache(max_age_s=0.0)
    short_lived.put(("alpha", 0), "stale-brief-output")

    second = short_lived.get(("alpha", 0))

    assert second is None, "expired entry must report as miss"
    assert short_lived.stats().misses == 1


def test_lru_eviction() -> None:
    """Exceeding the bound evicts the oldest entry."""
    from kairix.core.search.brief_output_cache import BriefOutputCache

    cache = BriefOutputCache(max_entries=2)
    cache.put(("a", 0), "A")
    cache.put(("b", 0), "B")
    cache.put(("c", 0), "C")  # evicts "a"

    stats = cache.stats()
    assert stats.evictions == 1
    assert stats.size == 2
    assert cache.get(("a", 0)) is None


def test_clear_invalidates_all() -> None:
    generate = _RecordingGenerate()
    deps = _healthy_deps(generate)

    run_brief("builder", deps=deps)
    reset_brief_output_cache()
    run_brief("builder", deps=deps)

    assert len(generate.calls) == 2, (
        f"expected two delegations across a reset; saw {len(generate.calls)} — clear may not be invalidating entries."
    )


def test_different_agent_different_key() -> None:
    """Same budget, different agent must produce distinct cache entries.

    Sabotage proof: dropping ``agent`` from
    :func:`make_brief_cache_key`'s tuple makes this fail — the second
    agent's call would hit the first agent's cached output.
    """
    generate = _RecordingGenerate()
    deps = _healthy_deps(generate)

    run_brief("builder", deps=deps)
    run_brief("shape", deps=deps)

    assert len(generate.calls) == 2, (
        f"expected distinct cache entries for different agents — "
        f"saw {len(generate.calls)} generate calls (agents collapsed into one slot)."
    )
    stats = get_brief_output_cache().stats()
    assert stats.size == 2


def test_health_field_is_kairix_health_shape() -> None:
    """Round-trip sanity check that the cached BriefOutput preserves health.

    Not strictly a cache test — just verifies that retrieving from the
    cache returns the same .health type so callers downstream don't
    crash on a missing field after cache wiring.
    """
    generate = _RecordingGenerate()
    deps = _healthy_deps(generate)

    first = run_brief("growth", deps=deps)
    second = run_brief("growth", deps=deps)

    assert isinstance(first.health, KairixHealth)
    assert isinstance(second.health, KairixHealth)
