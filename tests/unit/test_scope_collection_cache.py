"""Unit tests for :class:`ScopeCollectionCache` (R2, #388).

The MCP server's executor threads share one SQLite Connection on the
topology_v2 collection resolver. The cache turns repeated identical
``(agent, scope)`` resolves into memory lookups so the threads don't
serialise on SQLite's lock under load.

Each test pins one observable behaviour:

* ``test_hit_short_circuits_inner_resolver`` — second call with the
  same key never touches the wrapped resolver.
* ``test_miss_calls_inner_and_caches`` — first call delegates and the
  result is stored for the next call.
* ``test_expiry_drops_entry_and_returns_fresh`` — entry past TTL is
  treated as a miss and the inner resolver is invoked again.
* ``test_lru_eviction_when_max_entries_exceeded`` — adding one more
  key beyond the bound evicts the oldest.
* ``test_clear_invalidates_all_entries`` — explicit clear() forces
  the next call to delegate.
* ``test_validate_explicit_passes_through_uncached`` — the
  explicit-collections path is delegated 1:1 every time.

Sabotage proof (executed during development): mutating
``ScopeCollectionCache.resolve`` to always invoke the inner resolver
breaks ``test_hit_short_circuits_inner_resolver`` because the inner
call count jumps from 1 to 2.
"""

from __future__ import annotations

from typing import Any

import pytest

from kairix.core.search.scope_collection_cache import ScopeCollectionCache

pytestmark = pytest.mark.unit


class _RecordingResolver:
    """Stand-in for :class:`TopologyV2CollectionResolver` that records calls."""

    def __init__(self, value: list[str] | None) -> None:
        self._value = value
        self.resolve_calls: list[tuple[str | None, object]] = []
        self.validate_calls: list[tuple[Any, ...]] = []

    def resolve(self, agent: str | None, scope: object) -> list[str] | None:
        self.resolve_calls.append((agent, scope))
        return self._value

    def validate_explicit(self, *args: Any, **kwargs: Any) -> str:
        self.validate_calls.append((args, kwargs))
        return "validated"


class _ManualClock:
    """Controllable clock so TTL behaviour is tested without sleep()."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_hit_short_circuits_inner_resolver() -> None:
    inner = _RecordingResolver(["sharepoint", "obsidian"])
    clock = _ManualClock()
    cache = ScopeCollectionCache(inner, clock=clock)  # type: ignore[arg-type] — _ManualClock is structurally compatible with Callable[[], float]; mypy can't see the __call__ duck-type.

    first = cache.resolve(agent="shape", scope="shared+agent")
    second = cache.resolve(agent="shape", scope="shared+agent")

    assert first == ["sharepoint", "obsidian"]
    assert second == first
    assert len(inner.resolve_calls) == 1, "second call must short-circuit"


def test_miss_calls_inner_and_caches() -> None:
    inner = _RecordingResolver(["sharepoint"])
    clock = _ManualClock()
    cache = ScopeCollectionCache(inner, clock=clock)  # type: ignore[arg-type] — _ManualClock is structurally compatible with Callable[[], float]; mypy can't see the __call__ duck-type.

    result = cache.resolve(agent="builder", scope="agent")
    assert result == ["sharepoint"]
    assert inner.resolve_calls == [("builder", "agent")]
    stats = cache.stats()
    assert stats["misses"] == 1
    assert stats["hits"] == 0
    assert stats["size"] == 1


def test_expiry_drops_entry_and_returns_fresh() -> None:
    inner = _RecordingResolver(["sharepoint"])
    clock = _ManualClock()
    cache = ScopeCollectionCache(inner, max_age_s=60.0, clock=clock)  # type: ignore[arg-type] — _ManualClock is structurally compatible with Callable[[], float]; mypy can't see the __call__ duck-type.

    cache.resolve(agent="shape", scope="agent")
    clock.advance(120.0)  # past 60s TTL
    cache.resolve(agent="shape", scope="agent")

    assert len(inner.resolve_calls) == 2, "expired entry must re-delegate"


def test_lru_eviction_when_max_entries_exceeded() -> None:
    inner = _RecordingResolver(["sharepoint"])
    clock = _ManualClock()
    cache = ScopeCollectionCache(inner, max_entries=2, clock=clock)  # type: ignore[arg-type] — _ManualClock is structurally compatible with Callable[[], float]; mypy can't see the __call__ duck-type.

    cache.resolve(agent="a", scope="agent")
    cache.resolve(agent="b", scope="agent")
    cache.resolve(agent="c", scope="agent")  # evicts oldest = a

    stats = cache.stats()
    assert stats["evictions"] == 1
    assert stats["size"] == 2

    # 'a' was evicted so calling it again hits the inner resolver.
    cache.resolve(agent="a", scope="agent")
    assert len(inner.resolve_calls) == 4


def test_clear_invalidates_all_entries() -> None:
    inner = _RecordingResolver(["sharepoint"])
    cache = ScopeCollectionCache(inner)
    cache.resolve(agent="shape", scope="agent")
    cache.clear()
    cache.resolve(agent="shape", scope="agent")
    assert len(inner.resolve_calls) == 2


def test_validate_explicit_passes_through_uncached() -> None:
    inner = _RecordingResolver(["sharepoint"])
    cache = ScopeCollectionCache(inner)
    out_a = cache.validate_explicit("foo", explicit=["sharepoint"])
    out_b = cache.validate_explicit("foo", explicit=["sharepoint"])
    assert out_a == out_b == "validated"
    # No caching on validate — every call delegates.
    assert len(inner.validate_calls) == 2
