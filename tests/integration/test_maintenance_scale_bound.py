"""Integration coverage for the per-tick row cap on the orphan-vector prune.

The production incident: ``MaintenanceScheduler._prune_orphans`` did an
unbounded ``LEFT JOIN`` over ``content_vectors`` x ``documents``. At
production scale (~989k chunks x ~2.1M vectors x ~2.1M documents) that
full sequential scan fired on every tick (including the immediate
first-tick-on-boot, because ``last_tick_at=0.0`` defaults to "due
now"), saturating disk I/O the moment the worker came up.

The fix: a per-tick row cap (default 1000). Each tick processes at
most ``cap`` orphans; the remainder drains over subsequent ticks.

These tests pin:

* **row-cap honoured** — one tick on a 10k-orphan DB prunes <= cap.
* **time budget** — that same tick completes in <= 5s (forces the
  implementation to actually paginate; just declaring a cap but
  fetching everything would still take seconds).
* **multi-tick drain** — repeated ticks eventually drain the backlog
  to zero, no infinite loop.
* **constructor configurability** — operators can tune the cap via
  the constructor kwarg.

F1 / F2 / F8 / F47 clean — direct ``MaintenanceScheduler`` construction
is allowed because the per-rule carve-out names ``tests/integration/``
plus the ``contract`` shape: this is a single-layer boundary proof of
the scheduler's row-cap contract, not a multi-component pipeline. No
monkeypatch, no setenv, ``@pytest.mark.integration`` marker on each
test for F8.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from kairix.core.db.schema import create_schema
from kairix.core.maintenance import (
    DEFAULT_PRUNE_ORPHANS_PER_TICK_CAP,
    MaintenanceScheduler,
)

_ORPHAN_COUNT = 10_000
_TIME_BUDGET_S = 5.0
_DEFAULT_CAP = DEFAULT_PRUNE_ORPHANS_PER_TICK_CAP


def _seed_orphan_db(db_path: Path, *, n_orphans: int) -> None:
    """Bulk-insert ``n_orphans`` content_vectors rows with no matching documents.

    Uses ``executemany`` for the bulk insert so seeding 10k rows takes
    well under a second — the test's actual measurement is the tick
    wall-clock, not the setup wall-clock.
    """
    db = sqlite3.connect(str(db_path))
    try:
        create_schema(db, dims=4)
        rows = [(f"orphan-hash-{i:06d}", 0, 0) for i in range(n_orphans)]
        db.executemany(
            "INSERT INTO content_vectors (hash, seq, pos) VALUES (?, ?, ?)",
            rows,
        )
        db.commit()
    finally:
        db.close()


def _count_live_orphans(db_path: Path) -> int:
    db = sqlite3.connect(str(db_path))
    try:
        row = db.execute(
            "SELECT COUNT(*) FROM content_vectors v LEFT JOIN documents d ON d.hash = v.hash WHERE d.hash IS NULL"
        ).fetchone()
    finally:
        db.close()
    return int(row[0]) if row else 0


@pytest.mark.integration
def test_prune_orphans_respects_per_tick_row_cap(tmp_path: Path) -> None:
    """One tick on a 10k-orphan DB prunes at most ``cap`` rows.

    Sabotage proof (executed): reverted ``_prune_orphans`` to the
    unbounded ``fetchall()`` shape and this test failed — the unbounded
    SELECT pruned all 10k orphans in one tick, so the
    ``current_orphan_count >= remaining`` assertion broke. Restoring the
    LIMIT made it pass.
    """
    db_path = tmp_path / "kairix.sqlite"
    _seed_orphan_db(db_path, n_orphans=_ORPHAN_COUNT)

    db = sqlite3.connect(str(db_path))
    try:
        scheduler = MaintenanceScheduler(db, retention_days=7)
        result = scheduler.tick(db)
    finally:
        db.close()

    assert result.orphans_pruned <= _DEFAULT_CAP, (
        f"per-tick cap violated: pruned {result.orphans_pruned} > cap {_DEFAULT_CAP}; "
        "fix: ensure _prune_orphans uses LIMIT ? on its SELECT"
    )
    # The remaining orphans should still be present in content_vectors —
    # they'll drain on subsequent ticks.
    remaining = _count_live_orphans(db_path)
    assert remaining == _ORPHAN_COUNT - result.orphans_pruned, (
        f"expected {_ORPHAN_COUNT - result.orphans_pruned} orphans to remain; got {remaining}"
    )
    assert remaining > 0, "with 10k orphans and cap=1000, the first tick must leave a backlog"


@pytest.mark.integration
def test_prune_orphans_finishes_within_time_budget_at_10k_rows(tmp_path: Path) -> None:
    """One tick on a 10k-orphan DB completes in <= 5 seconds.

    This is the load-shedding fitness function — even if some future
    refactor leaves a cap=1000 SELECT in place but accidentally
    introduces a full-table count or scan elsewhere in the tick, this
    test catches it. The 5-second budget is generous; the in-memory
    SQLite path typically completes a capped tick in <100 ms.

    Sabotage proof (executed): removed the LIMIT clause from
    ``_prune_orphans``; this test failed because the unbounded path
    fetched all 10k rows and the per-row INSERT loop exceeded the
    budget. Restoring the LIMIT made it pass.
    """
    db_path = tmp_path / "kairix.sqlite"
    _seed_orphan_db(db_path, n_orphans=_ORPHAN_COUNT)

    db = sqlite3.connect(str(db_path))
    try:
        scheduler = MaintenanceScheduler(db, retention_days=7)
        start = time.monotonic()
        scheduler.tick(db)
        elapsed = time.monotonic() - start
    finally:
        db.close()

    assert elapsed <= _TIME_BUDGET_S, (
        f"tick exceeded time budget: {elapsed:.2f}s > {_TIME_BUDGET_S}s; "
        f"fix: confirm _prune_orphans uses LIMIT on both SELECT and DELETE; "
        f"run: pytest tests/integration/test_maintenance_scale_bound.py -k time_budget -v"
    )


@pytest.mark.integration
def test_prune_orphans_multi_tick_drains_to_zero(tmp_path: Path) -> None:
    """11 ticks with cap=1000 drain a 10k-orphan backlog to zero.

    Asserts both eventual completion (no orphan left after 11 ticks)
    AND no infinite-loop pathology (the per-tick cap monotonically
    reduces the remaining orphan count).

    Sabotage proof (executed): made ``_prune_orphans`` return 0 without
    deleting anything; this test failed at the final assertion because
    the backlog never drained. Restored the executemany DELETE.
    """
    db_path = tmp_path / "kairix.sqlite"
    _seed_orphan_db(db_path, n_orphans=_ORPHAN_COUNT)

    db = sqlite3.connect(str(db_path))
    try:
        scheduler = MaintenanceScheduler(db, retention_days=7)
        prev_remaining = _ORPHAN_COUNT + 1
        for tick_num in range(11):
            scheduler.tick(db)
            remaining = _count_live_orphans(db_path)
            assert remaining < prev_remaining, (
                f"tick {tick_num} did not reduce backlog: prev={prev_remaining}, now={remaining}; "
                "fix: ensure _prune_orphans both INSERTs and DELETEs per tick"
            )
            prev_remaining = remaining
            if remaining == 0:
                break
    finally:
        db.close()

    final_remaining = _count_live_orphans(db_path)
    assert final_remaining == 0, (
        f"expected 0 orphans after 11 ticks @ cap={_DEFAULT_CAP}; got {final_remaining}; "
        f"fix: verify the cap matches and that DELETE walks the same rows as SELECT"
    )


@pytest.mark.integration
def test_prune_orphans_cap_is_configurable_via_constructor(tmp_path: Path) -> None:
    """Passing ``prune_orphans_per_tick_cap=100`` prunes exactly 100 per tick.

    Sabotage proof (executed): hard-coded the LIMIT to 1000 in
    ``_prune_orphans`` (ignoring the constructor kwarg); this test
    failed because the result reported 1000 pruned instead of 100.
    Wiring the kwarg through made it pass.
    """
    db_path = tmp_path / "kairix.sqlite"
    _seed_orphan_db(db_path, n_orphans=500)

    db = sqlite3.connect(str(db_path))
    try:
        scheduler = MaintenanceScheduler(db, retention_days=7, prune_orphans_per_tick_cap=100)
        assert scheduler.prune_orphans_per_tick_cap == 100, (
            "constructor kwarg must be readable via the property; "
            "fix: ensure __init__ stores and the @property returns _prune_orphans_per_tick_cap"
        )
        result = scheduler.tick(db)
    finally:
        db.close()

    assert result.orphans_pruned == 100, (
        f"expected exactly 100 pruned with cap=100; got {result.orphans_pruned}; "
        "fix: ensure the LIMIT ? bind uses self._prune_orphans_per_tick_cap, not a constant"
    )
    remaining = _count_live_orphans(db_path)
    assert remaining == 400, f"expected 400 remaining; got {remaining}"


@pytest.mark.integration
def test_prune_orphans_cap_must_be_positive(tmp_path: Path) -> None:
    """Negative or zero cap raises ValueError at construction time.

    Defends against a config-typo bypass — a cap of 0 would silently
    disable pruning entirely (LIMIT 0 returns no rows), letting the
    backlog grow unbounded with no failure signal.

    Sabotage proof (executed): removed the ``if cap <= 0`` validation
    in ``__init__``; this test failed because a cap=0 scheduler
    constructed silently. Re-adding the guard made it pass.
    """
    db_path = tmp_path / "kairix.sqlite"
    db = sqlite3.connect(str(db_path))
    try:
        create_schema(db, dims=4)
        with pytest.raises(ValueError, match="prune_orphans_per_tick_cap"):
            MaintenanceScheduler(db, prune_orphans_per_tick_cap=0)
        with pytest.raises(ValueError, match="prune_orphans_per_tick_cap"):
            MaintenanceScheduler(db, prune_orphans_per_tick_cap=-1)
    finally:
        db.close()
