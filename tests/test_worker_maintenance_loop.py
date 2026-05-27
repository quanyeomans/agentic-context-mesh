"""KFEAT-021 Phase 1 — worker.py maintenance-loop wiring unit tests.

The integration / E2E tests cover the composed end-to-end path. These
unit tests pin the worker.py-side wrappers in isolation so the
``maintenance_loop_deps`` injection seam, the state persistence, and
the cadence + flag gating each have direct coverage.

Test discipline:
  * F1 / F2 clean — uses ``MaintenanceLoopDeps`` injection (no
    monkey-patch, no setenv).
  * F8 — every test carries ``@pytest.mark.unit``.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from kairix.core.db.schema import create_schema
from kairix.core.maintenance import MaintenanceTickResult
from kairix.worker import (
    MaintenanceLoopDeps,
    maybe_run_maintenance_loop_tick,
    run_maintenance_loop_tick,
)
from kairix.worker_state import WorkerPhase, WorkerState

pytestmark = pytest.mark.unit


@dataclass
class _Transitions:
    """Capture phase transitions a test triggered for assertion."""

    seen: list[WorkerPhase] = field(default_factory=list)

    def transition(self, phase: WorkerPhase) -> None:
        self.seen.append(phase)


@dataclass
class _WriteCapture:
    """Capture write_state calls."""

    writes: list[tuple[WorkerState, Path]] = field(default_factory=list)

    def write(self, state: WorkerState, path: Path) -> None:
        # Snapshot the relevant fields so later mutations don't leak in.
        self.writes.append(
            (
                WorkerState(
                    last_maintenance_tick_at=state.last_maintenance_tick_at,
                    last_maintenance_orphans_pruned=state.last_maintenance_orphans_pruned,
                    last_maintenance_pruned_table_size=state.last_maintenance_pruned_table_size,
                    last_maintenance_elapsed_ms=state.last_maintenance_elapsed_ms,
                ),
                path,
            )
        )


def _seeded_db_path(tmp_path: Path) -> Path:
    db_path = tmp_path / "kairix.sqlite"
    db = sqlite3.connect(str(db_path))
    try:
        create_schema(db, dims=4)
        db.execute("INSERT INTO content_vectors (hash, seq, pos) VALUES ('orphan-1', 0, 0)")
        db.commit()
    finally:
        db.close()
    return db_path


# ---------------------------------------------------------------------------
# run_maintenance_loop_tick
# ---------------------------------------------------------------------------


def test_run_maintenance_loop_tick_off_returns_none(tmp_path: Path) -> None:
    """Flag-OFF short-circuit returns None without opening the DB.

    Sabotage proof: drop the flag check and the test sees a
    MaintenanceTickResult instead of None.
    """
    db_path = _seeded_db_path(tmp_path)
    deps = MaintenanceLoopDeps(
        flag_reader=lambda _name: False,
        db_factory=lambda: sqlite3.connect(str(db_path)),
        retention_days_resolver=lambda: 7,
    )
    assert run_maintenance_loop_tick(deps) is None


def test_run_maintenance_loop_tick_on_returns_envelope(tmp_path: Path) -> None:
    """Flag-ON returns the MaintenanceTickResult envelope."""
    db_path = _seeded_db_path(tmp_path)
    deps = MaintenanceLoopDeps(
        flag_reader=lambda _name: True,
        db_factory=lambda: sqlite3.connect(str(db_path)),
        retention_days_resolver=lambda: 7,
    )
    result = run_maintenance_loop_tick(deps)
    assert isinstance(result, MaintenanceTickResult)
    assert result.orphans_pruned == 1


def test_run_maintenance_loop_tick_swallows_factory_exception(tmp_path: Path) -> None:
    """An exception raised inside the tick path is caught and logged.

    The wrapper returns None so the worker loop continues to the next
    iteration. Sabotage proof: remove the try/except and the test sees
    the exception propagate.
    """
    db_path = _seeded_db_path(tmp_path)

    def _boom_factory(_db: Any, _retention: int, _cap: int) -> Any:
        raise RuntimeError("simulated scheduler-construction failure")

    deps = MaintenanceLoopDeps(
        flag_reader=lambda _name: True,
        db_factory=lambda: sqlite3.connect(str(db_path)),
        retention_days_resolver=lambda: 7,
        scheduler_factory=_boom_factory,
    )
    assert run_maintenance_loop_tick(deps) is None


def test_default_scheduler_factory_runs_through_public_wrapper(tmp_path: Path) -> None:
    """Exercise the production default scheduler factory via the public wrapper.

    Constructs a :class:`MaintenanceLoopDeps` that omits the
    ``scheduler_factory`` kwarg so the default factory runs end-to-end.
    Sabotage proof: if the default factory raises on the production
    path, the wrapper returns None and the orphan stays in
    content_vectors.
    """
    db_path = _seeded_db_path(tmp_path)
    deps = MaintenanceLoopDeps(
        flag_reader=lambda _name: True,
        db_factory=lambda: sqlite3.connect(str(db_path)),
        retention_days_resolver=lambda: 7,
        # NOTE: no scheduler_factory override — the public wrapper uses
        # the default ``_default_scheduler_factory``, which constructs a
        # real MaintenanceScheduler.
    )
    result = run_maintenance_loop_tick(deps)
    assert isinstance(result, MaintenanceTickResult)
    assert result.orphans_pruned == 1


# ---------------------------------------------------------------------------
# maybe_run_maintenance_loop_tick — cadence + state persistence
# ---------------------------------------------------------------------------


def test_maybe_run_skips_when_not_due(tmp_path: Path) -> None:
    """Cadence inner gate — returns last_tick_at unchanged when interval not elapsed."""
    transitions = _Transitions()
    writes = _WriteCapture()
    state = WorkerState(last_maintenance_tick_at=1_000_000.0)
    deps = MaintenanceLoopDeps(flag_reader=lambda _name: True)
    new_tick = maybe_run_maintenance_loop_tick(
        deps=deps,
        transition=transitions.transition,
        state=state,
        state_path=tmp_path / "ws.json",
        write_state_fn=writes.write,
        now=1_000_100.0,  # only 100s elapsed; interval = 3600
        last_tick_at=1_000_000.0,
        interval_seconds=3600,
    )
    assert new_tick == 1_000_000.0, "not due — last_tick_at unchanged"
    assert transitions.seen == [], "transition must not fire when skipping"
    assert writes.writes == [], "no state write when skipping"


def test_maybe_run_fires_when_due_persists_state(tmp_path: Path) -> None:
    """When due AND flag ON, persist tick fields + return new last_tick_at."""
    db_path = _seeded_db_path(tmp_path)
    transitions = _Transitions()
    writes = _WriteCapture()
    state = WorkerState(last_maintenance_tick_at=0.0)
    deps = MaintenanceLoopDeps(
        flag_reader=lambda _name: True,
        db_factory=lambda: sqlite3.connect(str(db_path)),
        retention_days_resolver=lambda: 7,
    )
    new_tick = maybe_run_maintenance_loop_tick(
        deps=deps,
        transition=transitions.transition,
        state=state,
        state_path=tmp_path / "ws.json",
        write_state_fn=writes.write,
        now=1_000_000.0,
        last_tick_at=0.0,
        interval_seconds=3600,
    )
    assert new_tick == 1_000_000.0
    assert state.last_maintenance_orphans_pruned == 1
    assert state.last_maintenance_pruned_table_size == 1
    assert transitions.seen == [WorkerPhase.MAINTENANCE, WorkerPhase.IDLE]
    assert len(writes.writes) == 1
    captured_state, captured_path = writes.writes[0]
    assert captured_path == tmp_path / "ws.json"
    assert captured_state.last_maintenance_orphans_pruned == 1


def test_maybe_run_flag_off_does_not_advance_timestamp(tmp_path: Path) -> None:
    """Flag OFF: due but no tick fires → last_tick_at unchanged so OFF→ON flip fires immediately.

    Sabotage proof: if the helper advanced last_tick_at to ``now`` even
    when the wrapper returned None, the next loop iter post-flag-flip
    would wait a full interval before the first tick.
    """
    transitions = _Transitions()
    writes = _WriteCapture()
    state = WorkerState(last_maintenance_tick_at=0.0)
    deps = MaintenanceLoopDeps(flag_reader=lambda _name: False)
    new_tick = maybe_run_maintenance_loop_tick(
        deps=deps,
        transition=transitions.transition,
        state=state,
        state_path=tmp_path / "ws.json",
        write_state_fn=writes.write,
        now=1_000_000.0,
        last_tick_at=0.0,
        interval_seconds=3600,
    )
    assert new_tick == 0.0, "OFF branch must not advance last_tick_at"
    # State transitions still fire (MAINTENANCE -> IDLE) — those are
    # cheap and operator-visible; the no-op is just downstream.
    assert transitions.seen == [WorkerPhase.MAINTENANCE, WorkerPhase.IDLE]
    assert writes.writes == [], "OFF branch must not write WorkerState"
