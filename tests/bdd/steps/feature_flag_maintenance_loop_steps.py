"""Step definitions for feature_flag_maintenance_loop.feature.

KFEAT-021 Phase 1 introduces the ``maintenance_loop`` feature flag.
The brief calls for F54 both-branch coverage — these steps drive the
OFF and ON scenarios through the production composition surface:

* Both branches construct a real :class:`MaintenanceScheduler` against
  a tmp SQLite DB.
* The flag's value is pinned via :class:`FakeFeatureFlagResolver` from
  ``tests/fakes.py`` (F1 / F2-clean).
* The worker-loop dispatch boundary is exercised via
  :func:`kairix.worker.run_maintenance_loop_tick` — the same function
  the production worker calls. F46 / F47 compliance: this is the
  factory-equivalent for the maintenance surface.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from kairix.core.db.schema import create_schema
from kairix.core.maintenance import EVENT_TICK_COMPLETED
from kairix.worker import MaintenanceLoopDeps, run_maintenance_loop_tick
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.bdd

scenarios("../features/feature_flag_maintenance_loop.feature")

_FLAG_NAME = "maintenance_loop"


@dataclass
class _MaintenanceLoopCtx:
    """Per-scenario context. No module-level mutable state."""

    resolver: FakeFeatureFlagResolver | None = None
    flag_value: bool | None = None
    db_path: Path | None = None
    result: Any = None
    activation_logs: list[str] = field(default_factory=list)


@pytest.fixture
def maintenance_loop_ctx(tmp_path: Path) -> _MaintenanceLoopCtx:
    ctx = _MaintenanceLoopCtx()
    ctx.db_path = tmp_path / "kairix.sqlite"
    return ctx


def _bootstrap_db(db_path: Path, *, seed_orphan: bool = False) -> None:
    """Create the schema; optionally seed one orphan content_vectors row."""
    db = sqlite3.connect(str(db_path))
    try:
        create_schema(db, dims=4)
        if seed_orphan:
            # Orphan = content_vectors row whose hash has no matching
            # documents row. This is exactly the leak pattern from
            # document rewrites that KFEAT-021 Phase 1 prunes.
            db.execute(
                "INSERT INTO content_vectors (hash, seq, pos) VALUES (?, ?, ?)",
                ("orphan-hash-bdd", 0, 0),
            )
            db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Givens
# ---------------------------------------------------------------------------


@given(parsers.parse("the operator has the maintenance-loop flag set to {value}"))
def _operator_sets_flag(maintenance_loop_ctx: _MaintenanceLoopCtx, value: str) -> None:
    """Pin the flag via :class:`FakeFeatureFlagResolver`."""
    parsed = value.strip().lower() == "true"
    maintenance_loop_ctx.resolver = FakeFeatureFlagResolver().with_flag(_FLAG_NAME, parsed)
    maintenance_loop_ctx.flag_value = parsed
    assert maintenance_loop_ctx.db_path is not None
    _bootstrap_db(maintenance_loop_ctx.db_path, seed_orphan=False)


@given("the database has at least one orphan content_vectors row")
def _seed_orphan(maintenance_loop_ctx: _MaintenanceLoopCtx) -> None:
    """Seed one orphan row so the ON-branch tick has work to do."""
    assert maintenance_loop_ctx.db_path is not None
    _bootstrap_db(maintenance_loop_ctx.db_path, seed_orphan=True)


# ---------------------------------------------------------------------------
# Whens
# ---------------------------------------------------------------------------


@when("the worker loop reaches its maintenance-tick dispatch slot")
def _worker_loop_dispatches(
    maintenance_loop_ctx: _MaintenanceLoopCtx,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Invoke the production maintenance-tick dispatch helper.

    Routes through :func:`kairix.worker.run_maintenance_loop_tick` so
    the BDD step exercises the same composition the worker loop calls
    in production — F46 compliance.
    """
    assert maintenance_loop_ctx.resolver is not None, "Given step must run before When"
    assert maintenance_loop_ctx.db_path is not None
    resolver = maintenance_loop_ctx.resolver
    db_path = maintenance_loop_ctx.db_path

    deps = MaintenanceLoopDeps(
        flag_reader=resolver.get,
        db_factory=lambda: sqlite3.connect(str(db_path)),
        retention_days_resolver=lambda: 7,
    )
    with caplog.at_level(logging.INFO, logger="kairix.maintenance"):
        maintenance_loop_ctx.result = run_maintenance_loop_tick(deps)
    maintenance_loop_ctx.activation_logs = [rec.getMessage() for rec in caplog.records]


# ---------------------------------------------------------------------------
# Thens
# ---------------------------------------------------------------------------


@then("no MaintenanceScheduler.tick fires")
def _no_tick(maintenance_loop_ctx: _MaintenanceLoopCtx) -> None:
    """OFF branch: the production wrapper returns None when the flag is OFF."""
    assert maintenance_loop_ctx.result is None, f"OFF branch should return None; got {maintenance_loop_ctx.result!r}"


@then("the content_vectors_pruned table stays empty")
def _pruned_table_empty(maintenance_loop_ctx: _MaintenanceLoopCtx) -> None:
    assert maintenance_loop_ctx.db_path is not None
    db = sqlite3.connect(str(maintenance_loop_ctx.db_path))
    try:
        count = db.execute("SELECT COUNT(*) FROM content_vectors_pruned").fetchone()[0]
    finally:
        db.close()
    assert count == 0, f"OFF branch should leave content_vectors_pruned empty; got {count}"


@then("a MaintenanceScheduler.tick fires")
def _tick_fires(maintenance_loop_ctx: _MaintenanceLoopCtx) -> None:
    """ON branch: a non-None MaintenanceTickResult envelope returned."""
    result = maintenance_loop_ctx.result
    assert result is not None, "ON branch should fire a tick; got None"
    assert hasattr(result, "orphans_pruned"), f"expected MaintenanceTickResult; got {result!r}"


@then("the orphan row is moved into content_vectors_pruned")
def _orphan_moved(maintenance_loop_ctx: _MaintenanceLoopCtx) -> None:
    assert maintenance_loop_ctx.db_path is not None
    db = sqlite3.connect(str(maintenance_loop_ctx.db_path))
    try:
        pruned_count = db.execute(
            "SELECT COUNT(*) FROM content_vectors_pruned WHERE hash = 'orphan-hash-bdd'"
        ).fetchone()[0]
        original_count = db.execute("SELECT COUNT(*) FROM content_vectors WHERE hash = 'orphan-hash-bdd'").fetchone()[0]
    finally:
        db.close()
    assert pruned_count == 1, f"expected orphan row in content_vectors_pruned; got {pruned_count}"
    assert original_count == 0, f"expected original orphan deleted; got {original_count}"


@then("the structured maintenance_tick_completed log event is emitted")
def _tick_completed_log(maintenance_loop_ctx: _MaintenanceLoopCtx) -> None:
    completed = [m for m in maintenance_loop_ctx.activation_logs if EVENT_TICK_COMPLETED in m]
    assert completed, (
        f"expected at least one event={EVENT_TICK_COMPLETED} log line; got: {maintenance_loop_ctx.activation_logs!r}"
    )
