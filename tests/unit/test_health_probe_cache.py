"""Unit tests for the health-probe cache (#396 W-B C6).

``run_brief`` calls ``probe_health(deps)`` at the top of every
invocation, which fans 4 dependency probes (secrets, embed, BM25,
neo4j) on daemon threads. The fan-out alone costs 200-400ms in
production. This cache turns repeat calls within the 10s TTL into
memory lookups.

Each test pins one observable behaviour:

* ``test_probe_health_cached_within_ttl`` — second call within 10s
  returns same instance, doesn't re-invoke the probe fns.
* ``test_probe_health_refresh_past_ttl`` — clock advanced 11s →
  re-invokes the probes.
* ``test_probe_health_clear`` — explicit clear forces refresh.

Sabotage proofs (executed during development):

* Replacing the cache TTL check with ``False`` (always cache hit)
  breaks ``test_probe_health_refresh_past_ttl`` — the clock advance
  no longer triggers a re-probe.
* Removing the ``probe_health`` call from the cache miss path returns
  ``None`` instead of a snapshot; ``test_probe_health_cached_within_ttl``
  fails because the cached instance has no fields populated.

The clock is injected via the public ``set_health_probe_cache_clock``
seam so the tests don't need real sleep / monkey-patching.
"""

from __future__ import annotations

import pytest

from kairix.core.health import (
    HealthDeps,
    reset_health_probe_cache,
    set_health_probe_cache_clock,
)
from kairix.use_cases.brief import (
    BriefDeps,
    run_brief,
)

pytestmark = pytest.mark.unit


class _ControllableClock:
    """Test clock: monotonic, advanced manually."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _counting_health_deps() -> tuple[HealthDeps, dict[str, int]]:
    """Build HealthDeps whose probes count every call.

    Returns the deps + the call-counter dict so the test can assert
    "we called secrets-probe N times".
    """
    counts: dict[str, int] = {"secrets": 0, "embed": 0, "bm25": 0, "neo4j": 0}

    def _secrets() -> bool:
        counts["secrets"] += 1
        return True

    def _embed() -> bool:
        counts["embed"] += 1
        return True

    def _bm25() -> bool:
        counts["bm25"] += 1
        return True

    def _neo4j() -> bool:
        counts["neo4j"] += 1
        return True

    deps = HealthDeps(
        secrets_loaded_fn=_secrets,
        embed_backend_available_fn=_embed,
        bm25_index_available_fn=_bm25,
        neo4j_available_fn=_neo4j,
    )
    return deps, counts


@pytest.fixture(autouse=True)
def _reset_cache_and_clock() -> None:
    """Each test starts with a fresh cache + real-time clock restored at teardown."""
    import time

    reset_health_probe_cache()
    yield
    reset_health_probe_cache()
    set_health_probe_cache_clock(time.time)


def test_probe_health_cached_within_ttl() -> None:
    """A second run_brief within 10s reuses the cached health snapshot."""
    clock = _ControllableClock()
    set_health_probe_cache_clock(clock)

    health_deps, counts = _counting_health_deps()
    deps = BriefDeps(
        generate_fn=lambda agent, **_: "# brief",
        briefing_dir_fn=lambda: None,
        sources_fn=lambda _agent: [],
        health_deps=health_deps,
    )

    run_brief("builder", deps=deps)
    # Pre-cache snapshot: every probe ran exactly once.
    assert counts["secrets"] == 1
    assert counts["embed"] == 1
    assert counts["bm25"] == 1

    clock.advance(5.0)  # within 10s TTL
    run_brief("builder", deps=deps)

    # No additional probe calls — the cache hit short-circuited.
    assert counts["secrets"] == 1, f"expected probe to run once within TTL; saw secrets count = {counts['secrets']}"
    assert counts["embed"] == 1
    assert counts["bm25"] == 1


def test_probe_health_refresh_past_ttl() -> None:
    """A second run_brief past the 10s TTL re-runs the probes."""
    clock = _ControllableClock()
    set_health_probe_cache_clock(clock)

    health_deps, counts = _counting_health_deps()
    deps = BriefDeps(
        generate_fn=lambda agent, **_: "# brief",
        briefing_dir_fn=lambda: None,
        sources_fn=lambda _agent: [],
        health_deps=health_deps,
    )

    run_brief("builder", deps=deps)
    clock.advance(11.0)  # past 10s TTL
    run_brief("builder", deps=deps)

    assert counts["secrets"] == 2, f"expected probes to refresh past TTL; saw secrets count = {counts['secrets']}"
    assert counts["embed"] == 2
    assert counts["bm25"] == 2


def test_probe_health_clear() -> None:
    """Explicit reset forces the next call to re-probe."""
    clock = _ControllableClock()
    set_health_probe_cache_clock(clock)

    health_deps, counts = _counting_health_deps()
    deps = BriefDeps(
        generate_fn=lambda agent, **_: "# brief",
        briefing_dir_fn=lambda: None,
        sources_fn=lambda _agent: [],
        health_deps=health_deps,
    )

    run_brief("builder", deps=deps)
    reset_health_probe_cache()
    run_brief("builder", deps=deps)

    assert counts["secrets"] == 2, f"reset must force re-probe; saw secrets count = {counts['secrets']}"


def test_probe_health_cache_hit_at_exact_ttl_boundary() -> None:
    """At EXACTLY the TTL boundary the cached snapshot is still served — the
    comparison is ``<=`` (inclusive), not ``<``.

    The default TTL is 10.0s; advancing the clock by exactly that amount must
    still be a cache hit so the probes don't re-run on the boundary tick.

    Sabotage-proof: change ``<=`` to ``<`` in cached_probe_health's TTL check
    and the boundary tick becomes a miss → the probes re-run → secrets count
    climbs to 2, failing the ``== 1`` assertion below.
    """
    clock = _ControllableClock()
    set_health_probe_cache_clock(clock)

    health_deps, counts = _counting_health_deps()
    deps = BriefDeps(
        generate_fn=lambda agent, **_: "# brief",
        briefing_dir_fn=lambda: None,
        sources_fn=lambda _agent: [],
        health_deps=health_deps,
    )

    run_brief("builder", deps=deps)  # stored at t=1000.0
    clock.advance(10.0)  # exactly the documented 10.0s TTL boundary
    run_brief("builder", deps=deps)

    assert counts["secrets"] == 1, f"boundary tick must still hit (<=); saw secrets count = {counts['secrets']}"
