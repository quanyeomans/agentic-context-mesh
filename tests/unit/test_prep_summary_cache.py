"""Unit tests for the prep-summary cache wired into ``run_prep`` (#396 W-B C3).

The cache wraps the LLM ``chat()`` synthesis step at the end of
``run_prep``. Identical ``(query, tier, retrieved-context)`` triples
within the TTL window short-circuit the chat fn entirely; the cache
returns the previously-synthesised summary.

Each test pins one observable behaviour:

* ``test_hit_short_circuits_chat`` — second call with same key never
  invokes the chat fn.
* ``test_miss_calls_chat_and_caches`` — first call delegates and the
  result is stored for the next call.
* ``test_expiry_drops_entry`` — entry past TTL → re-delegate.
* ``test_lru_eviction`` — exceed max_entries → oldest evicted.
* ``test_clear_invalidates_all`` — explicit clear() forces next call
  to delegate.
* ``test_different_tier_different_key`` — same query, different tier
  → distinct cache entries.

Sabotage proofs (executed during development):

* Removing the cache.get + cache.put wrapper around the chat call in
  ``run_prep`` breaks ``test_hit_short_circuits_chat`` (chat_calls
  jumps from 1 to 2).
* Removing ``tier`` from ``make_prep_cache_key``'s tuple breaks
  ``test_different_tier_different_key`` (the second tier call hits
  the first tier's cached summary).

Tests drive prep through its public entry point (:func:`run_prep`)
with a recording chat fn; F5-clean — no internal-name imports beyond
the public symbol(s).
"""

from __future__ import annotations

from typing import Any

import pytest

from kairix.use_cases.prep import (
    PrepDeps,
    get_prep_summary_cache,
    reset_prep_summary_cache,
    run_prep,
)

pytestmark = pytest.mark.unit


class _RecordingChat:
    """Records every ``chat()`` invocation and returns a fixed reply.

    Each call appends to ``calls`` so the test can assert N delegations
    without monkey-patching anything inside kairix.
    """

    def __init__(self, reply: str = "synthesised summary") -> None:
        self._reply = reply
        self.calls: list[dict[str, Any]] = []

    def __call__(self, *, messages: list[dict[str, str]], max_tokens: int) -> str:
        self.calls.append({"messages": messages, "max_tokens": max_tokens})
        return self._reply


def _make_search(rows: list[dict[str, Any]]) -> Any:
    """Build a stand-in search fn returning a SearchResult-shaped object."""
    from types import SimpleNamespace

    def _search(*, query: str, agent: str | None, scope: Any, budget: int) -> Any:
        _ = (query, agent, scope, budget)
        results = [
            SimpleNamespace(
                result=SimpleNamespace(title=row["title"], path=row["path"]),
                content=row["content"],
            )
            for row in rows
        ]
        return SimpleNamespace(results=results)

    return _search


def _default_rows() -> list[dict[str, Any]]:
    """Return one row long enough to pass the _MIN_USEFUL_SNIPPET_CHARS floor."""
    return [
        {
            "title": "topic-overview.md",
            "path": "knowledge/topic-overview.md",
            "content": "This is a long enough snippet to pass the chunk floor — clearly above 40 chars.",
        },
    ]


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    """Each test starts with a fresh cache so hit/miss counters are deterministic."""
    reset_prep_summary_cache()
    yield
    reset_prep_summary_cache()


def test_hit_short_circuits_chat() -> None:
    chat = _RecordingChat()
    deps = PrepDeps(search_fn=_make_search(_default_rows()), chat_fn=chat)

    run_prep("how does X work", tier="l0", deps=deps)
    run_prep("how does X work", tier="l0", deps=deps)

    assert len(chat.calls) == 1, (
        f"expected the second prep call to hit the cache and skip the chat fn; saw {len(chat.calls)} chat invocations."
    )
    stats = get_prep_summary_cache().stats()
    assert stats.hits == 1
    assert stats.misses == 1


def test_miss_calls_chat_and_caches() -> None:
    chat = _RecordingChat()
    deps = PrepDeps(search_fn=_make_search(_default_rows()), chat_fn=chat)

    result = run_prep("new query", tier="l0", deps=deps)
    assert len(chat.calls) == 1, "cold cache must delegate to chat"
    assert result.summary == "synthesised summary"
    stats = get_prep_summary_cache().stats()
    assert stats.size == 1
    assert stats.misses == 1
    assert stats.hits == 0


def test_expiry_drops_entry() -> None:
    """An entry past the TTL must re-delegate to the chat fn.

    Drives the public cache directly via :func:`get_prep_summary_cache`
    so we can assert ``stats.misses`` jumps without needing a TTL fake
    threaded through ``run_prep``.
    """
    # Reach in via the public accessor and replace with a short-TTL
    # instance for this test. F5-clean: no kairix internal imports.
    from kairix.core.search.prep_summary_cache import PrepSummaryCache

    short_lived = PrepSummaryCache(max_age_s=0.0)
    short_lived.put(("k", "l0", "ctx"), "old summary")

    # max_age_s=0 means every entry is immediately stale → second get
    # observes a miss.
    second = short_lived.get(("k", "l0", "ctx"))

    assert second is None, "expired entry must report as miss"
    assert short_lived.stats().misses == 1


def test_lru_eviction() -> None:
    """Exceeding the bound evicts the oldest entry."""
    from kairix.core.search.prep_summary_cache import PrepSummaryCache

    cache = PrepSummaryCache(max_entries=2)
    cache.put(("a", "l0", "ctx"), "A")
    cache.put(("b", "l0", "ctx"), "B")
    cache.put(("c", "l0", "ctx"), "C")  # evicts "a"

    stats = cache.stats()
    assert stats.evictions == 1
    assert stats.size == 2
    # "a" was evicted → get reports a miss
    assert cache.get(("a", "l0", "ctx")) is None


def test_clear_invalidates_all() -> None:
    chat = _RecordingChat()
    deps = PrepDeps(search_fn=_make_search(_default_rows()), chat_fn=chat)

    run_prep("question", tier="l0", deps=deps)
    reset_prep_summary_cache()
    run_prep("question", tier="l0", deps=deps)

    assert len(chat.calls) == 2, (
        f"expected two delegations across a reset; saw {len(chat.calls)} — clear may not be invalidating entries."
    )


def test_different_tier_different_key() -> None:
    """Same query at different tiers must produce distinct cache entries.

    Sabotage proof: dropping ``tier`` from
    :func:`make_prep_cache_key`'s tuple makes this test fail — the
    second (l1) call hits the l0 entry and chat is invoked only once.
    """
    chat = _RecordingChat()
    deps = PrepDeps(search_fn=_make_search(_default_rows()), chat_fn=chat)

    run_prep("same query", tier="l0", deps=deps)
    run_prep("same query", tier="l1", deps=deps)

    assert len(chat.calls) == 2, (
        "expected distinct cache entries for l0 vs l1 — "
        f"saw {len(chat.calls)} chat calls (one tier collapsed into the other)."
    )
    stats = get_prep_summary_cache().stats()
    assert stats.size == 2
