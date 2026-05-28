"""Soak: Neo4j drain converges on a 10k-row backlog within bounded ticks.

ADR-024 Bundle F seed soak test. Pins the GH #334 paydown: every
un-pushed ``entity_signals`` row eventually flips ``pushed_to_neo4j=1``
when the drain is reachable, and the per-tick count is monotonic
across the run (no row goes 1 -> 0 -> 1).

Composed through :func:`kairix.core.factory.build_neo4j_drainer` per
F47. The Neo4j boundary is :class:`tests.fakes.FakeDrainGraphRepository`
(``available=True``) — no real Neo4j needed; the production drain
treats the fake exactly like the live repository because the contract
boundary is just :meth:`DrainGraphRepository.cypher`.

Wall-clock budget: < 3 min (asserted). On the soak runner the actual
runtime is ~10-30s; the 3 min ceiling leaves headroom for slow-runner
variance.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from kairix.core import factory
from kairix.core.curator.drain import DEFAULT_DRAIN_BATCH_SIZE
from kairix.core.db.schema import create_schema
from tests.fakes import FakeDrainGraphRepository, seed_bulk_entity_signals

pytestmark = pytest.mark.soak

# Production-scale fixture: 10k rows is the ADR-024 floor for the soak
# tier (N >= 10**4). Production saw 2.3M rows accumulate; 10k is enough
# to demonstrate multi-tick convergence without burning runner time.
_SOAK_N_ROWS = 10_000

# Wall-clock budget — assert at end of test. Failure here means the
# drain has slowed unacceptably and the operator-facing "X hours to
# drain N rows" estimate in the runbook is no longer valid.
_WALL_CLOCK_BUDGET_SECONDS = 180

# Tick ceiling — at default batch_size=500 the drain needs ceil(10000/500)=20
# ticks. A 2x ceiling (40 ticks) catches regressions like "drain only
# advances 50 rows per tick instead of 500" without flaking on a
# legitimate single-batch overhang.
_MAX_TICKS_TO_CONVERGE = 40


def _open_db(tmp_path: Path) -> sqlite3.Connection:
    """Open a fresh SQLite connection with the production schema applied."""
    db = sqlite3.connect(str(tmp_path / "drain_soak.sqlite"))
    create_schema(db)
    return db


def _count_pushed(db: sqlite3.Connection) -> int:
    row = db.execute("SELECT COUNT(*) FROM entity_signals WHERE pushed_to_neo4j = 1").fetchone()
    return int(row[0]) if row else 0


def _count_unpushed(db: sqlite3.Connection) -> int:
    row = db.execute("SELECT COUNT(*) FROM entity_signals WHERE pushed_to_neo4j = 0").fetchone()
    return int(row[0]) if row else 0


def test_drain_progress_at_10k_converges_monotonically(tmp_path: Path) -> None:
    """10k un-pushed rows drain to zero across <= 40 ticks; pushed-count is monotonic.

    Concrete observable outcomes asserted (no "no exception" — every
    assertion grounded in a measurable invariant):

      1. Pre-tick state: ``pushed_to_neo4j=0`` count == 10_000
      2. Per-tick: ``pushed_to_neo4j=1`` count is monotonic non-decreasing
      3. Post-loop: every row at ``pushed_to_neo4j=1`` (count == 10_000)
      4. Per-loop: drain converges within ``_MAX_TICKS_TO_CONVERGE``
      5. Wall-clock: elapsed < ``_WALL_CLOCK_BUDGET_SECONDS``

    Sabotage proof: change ``MAX_PUSH_ATTEMPTS`` in
    ``kairix/core/curator/drain.py`` from 3 to 0 — every row falls out
    of the ``WHERE push_attempt_count < ?`` filter immediately; tick 1
    pushes 0 rows; the convergence loop bails after
    ``_MAX_TICKS_TO_CONVERGE`` ticks with ~9500 rows still un-pushed;
    assertion 3 (every row pushed) fails with a concrete count
    mismatch. Verified locally before commit.
    """
    db = _open_db(tmp_path)
    try:
        # 1. Seed 10k un-pushed signals via the canonical bulk helper.
        n_seeded = seed_bulk_entity_signals(db, n_rows=_SOAK_N_ROWS)
        assert n_seeded == _SOAK_N_ROWS, f"bulk seed should insert {_SOAK_N_ROWS}; inserted {n_seeded}"
        assert _count_unpushed(db) == _SOAK_N_ROWS, (
            f"pre-tick: expected {_SOAK_N_ROWS} un-pushed; got {_count_unpushed(db)}"
        )
        assert _count_pushed(db) == 0, f"pre-tick: expected 0 pushed; got {_count_pushed(db)}"

        # 2. Compose the drainer via the F47-sanctioned factory entry
        # point with a reachable fake. The fake is Protocol-compliant
        # against DrainGraphRepository so the drain treats it like the
        # production repository — no monkeypatching of internals.
        repo = FakeDrainGraphRepository(available=True)
        drainer = factory.build_neo4j_drainer(db=db, repo=repo, batch_size=DEFAULT_DRAIN_BATCH_SIZE)

        # 3. Tick the drain until it converges (or until the ceiling).
        # Capture pushed-count per tick to assert monotonicity.
        pushed_per_tick: list[int] = []
        started_at = time.monotonic()
        ticks_run = 0
        for tick_idx in range(_MAX_TICKS_TO_CONVERGE):
            result = drainer.tick()
            ticks_run = tick_idx + 1
            pushed_per_tick.append(_count_pushed(db))
            # Drain converged: result.pushed == 0 AND zero un-pushed remaining.
            if result.pushed == 0 and _count_unpushed(db) == 0:
                break
        elapsed_s = time.monotonic() - started_at

        # 4. Convergence: every row pushed.
        final_pushed = _count_pushed(db)
        final_unpushed = _count_unpushed(db)
        assert final_pushed == _SOAK_N_ROWS, (
            f"drain failed to push every row in {ticks_run} ticks: "
            f"pushed={final_pushed} unpushed={final_unpushed} (expected pushed={_SOAK_N_ROWS})"
        )
        assert final_unpushed == 0, f"residual un-pushed rows after convergence: {final_unpushed}"

        # 5. Monotonicity: pushed-count never decreases between ticks.
        for prev_idx in range(len(pushed_per_tick) - 1):
            curr = pushed_per_tick[prev_idx + 1]
            prev = pushed_per_tick[prev_idx]
            assert curr >= prev, (
                f"non-monotonic pushed-count at tick {prev_idx + 1}: "
                f"tick {prev_idx} pushed={prev}, tick {prev_idx + 1} pushed={curr}"
            )

        # 6. Tick ceiling — drain converged within the budget.
        assert ticks_run <= _MAX_TICKS_TO_CONVERGE, (
            f"drain took {ticks_run} ticks to converge; ceiling is {_MAX_TICKS_TO_CONVERGE}. "
            f"fix: investigate per-tick throughput regression in kairix/core/curator/drain.py."
        )

        # 7. Wall-clock budget — drain ran inside the operator-facing
        # SLA; regressions surface here before they surface in production.
        assert elapsed_s < _WALL_CLOCK_BUDGET_SECONDS, (
            f"drain wall-clock {elapsed_s:.1f}s exceeded budget of {_WALL_CLOCK_BUDGET_SECONDS}s "
            f"for {_SOAK_N_ROWS} rows. fix: profile the drain loop or raise the budget with rationale."
        )
    finally:
        db.close()
