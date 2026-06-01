"""Soak: ScopeProfileResolver.resolve(default_only=True) at production scale.

ADR-024 Soak tier seed test for GH #373. Pins the p95 latency budget for
the new default_in_scope filter when the underlying scope_entries surface
is at production scale (10K rows across 100 agents).

Per CLAUDE.md "Soak tier" section: production-scale soak tests live
under ``tests/soak/`` and carry ``pytestmark = pytest.mark.soak``. They
seed N >= 10**4 rows through the canonical fakes + factory and assert
concrete observable outcomes (row counts, wall-clock budgets) at
production scale. Excluded from Stage 2/3 per-commit CI; runs nightly in
``soak-suite.yml`` and on-demand via ``gh workflow run soak-suite.yml``.

Scaffolding pattern: xfail with strict=False until the production change
lands (the resolver doesn't accept ``default_only`` yet, so the call
would TypeError today); the impl agent removes the decorator inline.

F47 composition note: the production-side primitive under test
(``ScopeProfileResolver``) IS the construction-via-factory target — the
factory ``build_collection_resolver`` wires it. This soak test pins the
resolver directly because the resolver is the unit under test for the
latency assertion; the broader pipeline path is covered by the e2e test.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest

from kairix.core.db.schema import create_schema
from tests.fakes import seed_bulk_scope_entries

pytestmark = pytest.mark.soak
# Production-scale fixture per ADR-024 Soak tier — N >= 10**4 rows.
# 100 agents x 100 entries-each = 10_000 rows; matches the GH #373
# operator-facing target (production vault carries ~6 agents x ~10
# collections each, so 100x100 is ~17x the live profile).
_N_AGENTS = 100
_ENTRIES_PER_AGENT = 100
_TOTAL_ROWS = _N_AGENTS * _ENTRIES_PER_AGENT

# Resolve-call count + p95 budget. 1000 calls give a stable p95 sample.
_N_RESOLVE_CALLS = 1000
_P95_BUDGET_MS = 50.0
_WALL_CLOCK_BUDGET_SECONDS = 60.0


def _percentile(values: list[float], pct: float) -> float:
    """Compute the pct percentile of ``values`` (linear interpolation)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = (pct / 100.0) * (len(ordered) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(ordered) - 1)
    frac = idx - lo
    return ordered[lo] * (1 - frac) + ordered[hi] * frac


def _open_db(tmp_path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(str(tmp_path / "scope_soak.sqlite"))
    create_schema(db)
    return db


@pytest.mark.xfail(reason="impl pending — #373 / feature-flag topology_v2_default_in_scope", strict=False)
def test_default_only_resolve_under_10k_scope_entries_p95_50ms(tmp_path: Path) -> None:
    """10K scope_entries seeded → 1000 resolve(default_only=True) calls
    → p95 latency ≤ 50ms.

    Pins the production-facing SLA: an operator with the full 100-agent x
    100-collection-each topology gets a resolve call that fits inside the
    p95 budget for every search query.

    Asserts (all grounded in measurable invariants):

      1. Seeded row count == _TOTAL_ROWS (sanity check on the helper).
      2. Every resolve call returns a non-empty result (filter doesn't
         silently zero-out the in-default subset under load).
      3. p95 latency < _P95_BUDGET_MS.
      4. Wall-clock < _WALL_CLOCK_BUDGET_SECONDS.

    Sabotage anchor (post-impl): change the filter SQL to scan twice (e.g.
    add an unindexed OR clause); p95 will breach 50ms and the assertion
    fails with the concrete latency value.
    """
    # Lazy import — the production class doesn't accept default_only yet,
    # so importing at module scope is fine; the type error surfaces at
    # call time and the xfail captures it.
    from kairix.core.connectors.scope_profile_resolver import ScopeProfileResolver

    db = _open_db(tmp_path)
    try:
        # 1. Seed the production-scale scope.
        inserted = seed_bulk_scope_entries(
            db,
            n_agents=_N_AGENTS,
            entries_per_agent=_ENTRIES_PER_AGENT,
            default_in_scope_ratio=0.7,
        )
        assert inserted == _TOTAL_ROWS, f"seed_bulk_scope_entries should insert {_TOTAL_ROWS}; inserted {inserted}"

        resolver = ScopeProfileResolver(db)

        # 2. Drive _N_RESOLVE_CALLS resolves; each one against a randomly
        # chosen agent (deterministic via the modulo-cycle, not RNG).
        latencies_ms: list[float] = []
        started_at = time.monotonic()
        for call_idx in range(_N_RESOLVE_CALLS):
            actor_id = f"agent-soak-{call_idx % _N_AGENTS:04d}"
            t0 = time.perf_counter()
            scope: Any = resolver.resolve(actors=(actor_id,), default_only=True)
            t1 = time.perf_counter()
            latencies_ms.append((t1 - t0) * 1000.0)
            assert len(scope.collections) > 0, (
                f"call {call_idx} for {actor_id}: resolve(default_only=True) "
                f"returned empty under production scale — filter degenerate"
            )
        elapsed_s = time.monotonic() - started_at

        # 3. p95 budget.
        p95 = _percentile(latencies_ms, 95.0)
        assert p95 < _P95_BUDGET_MS, (
            f"resolve(default_only=True) p95 {p95:.2f}ms exceeded budget "
            f"{_P95_BUDGET_MS:.2f}ms at {_TOTAL_ROWS} scope_entries. "
            f"fix: profile the default_in_scope filter or add a covering index."
        )

        # 4. Wall-clock budget.
        assert elapsed_s < _WALL_CLOCK_BUDGET_SECONDS, (
            f"soak wall-clock {elapsed_s:.1f}s exceeded budget "
            f"{_WALL_CLOCK_BUDGET_SECONDS}s for {_N_RESOLVE_CALLS} calls. "
            f"fix: investigate cold-cache vs warm-cache resolve perf."
        )
    finally:
        db.close()
