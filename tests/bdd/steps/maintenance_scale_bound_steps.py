"""Step definitions for maintenance_scale_bound.feature.

Drives the production maintenance-loop dispatch helper
(:func:`kairix.worker.run_maintenance_loop_tick`) with the per-tick
cap injected via :class:`MaintenanceLoopDeps.prune_orphans_per_tick_cap`.
F46 compliance: the BDD step composes the worker-side dispatcher
(call-graph depth 1), not a raw ``MaintenanceScheduler(...)``.
F1 / F2 clean — flag value pinned via :class:`FakeFeatureFlagResolver`.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.core.db.schema import create_schema
from kairix.worker import MaintenanceLoopDeps, run_maintenance_loop_tick
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.bdd


@dataclass
class _ScaleBoundCtx:
    """Per-scenario context for the scale-bound feature."""

    db_path: Path | None = None
    seeded_orphans: int = 0
    cap: int = 1000
    result: Any = None


@pytest.fixture
def scale_bound_ctx(tmp_path: Path) -> _ScaleBoundCtx:
    ctx = _ScaleBoundCtx()
    ctx.db_path = tmp_path / "kairix.sqlite"
    return ctx


def _seed_orphans(db_path: Path, n: int) -> None:
    db = sqlite3.connect(str(db_path))
    try:
        create_schema(db, dims=4)
        rows = [(f"orphan-bdd-{i:06d}", 0, 0) for i in range(n)]
        db.executemany(
            "INSERT INTO content_vectors (hash, seq, pos) VALUES (?, ?, ?)",
            rows,
        )
        db.commit()
    finally:
        db.close()


@given(parsers.parse("the database holds {count:d} orphan content_vectors rows"))
def _seed_orphan_rows(scale_bound_ctx: _ScaleBoundCtx, count: int) -> None:
    assert scale_bound_ctx.db_path is not None
    _seed_orphans(scale_bound_ctx.db_path, count)
    scale_bound_ctx.seeded_orphans = count


@given(parsers.parse("the maintenance scheduler is configured with a per-tick cap of {cap:d}"))
def _configure_cap(scale_bound_ctx: _ScaleBoundCtx, cap: int) -> None:
    scale_bound_ctx.cap = cap


@when("one maintenance tick runs")
def _run_one_tick(scale_bound_ctx: _ScaleBoundCtx) -> None:
    assert scale_bound_ctx.db_path is not None
    resolver = FakeFeatureFlagResolver().with_flag("maintenance_loop", True)
    db_path = scale_bound_ctx.db_path
    deps = MaintenanceLoopDeps(
        flag_reader=resolver.get,
        db_factory=lambda: sqlite3.connect(str(db_path)),
        retention_days_resolver=lambda: 7,
        prune_orphans_per_tick_cap=scale_bound_ctx.cap,
    )
    scale_bound_ctx.result = run_maintenance_loop_tick(deps)


@then(parsers.parse("the tick reports at most {cap:d} rows pruned"))
def _tick_reports_bounded(scale_bound_ctx: _ScaleBoundCtx, cap: int) -> None:
    result = scale_bound_ctx.result
    assert result is not None, "expected a populated MaintenanceTickResult"
    assert result.orphans_pruned <= cap, (
        f"per-tick cap violated: pruned {result.orphans_pruned} > cap {cap}; "
        "fix: ensure _prune_orphans uses LIMIT ? on its SELECT"
    )


@then("the remaining orphans stay in content_vectors for the next tick")
def _remaining_orphans_stay(scale_bound_ctx: _ScaleBoundCtx) -> None:
    assert scale_bound_ctx.db_path is not None
    db = sqlite3.connect(str(scale_bound_ctx.db_path))
    try:
        remaining = db.execute(
            "SELECT COUNT(*) FROM content_vectors v LEFT JOIN documents d ON d.hash = v.hash WHERE d.hash IS NULL"
        ).fetchone()[0]
    finally:
        db.close()
    assert scale_bound_ctx.result is not None
    expected_remaining = scale_bound_ctx.seeded_orphans - scale_bound_ctx.result.orphans_pruned
    assert remaining == expected_remaining, (
        f"expected {expected_remaining} orphans remaining; got {remaining}; "
        "fix: confirm DELETE walks the same rowset as SELECT, not an unbounded subquery"
    )
    assert remaining > 0, "scenario assumes seeded > cap so a backlog must remain"
