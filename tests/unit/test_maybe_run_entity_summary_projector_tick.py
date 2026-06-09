"""Unit tests for the worker-loop entity-summary projector tick
(ADR-036 §Worker, #460 / #462 close-out).

The wrapper mirrors :func:`maybe_run_maintenance_loop_tick`:

* OUTER gate — the ``entity_summary_indexing_enabled`` flag check
  lives inside the dispatcher; the wrapper passes through the deps so
  flag-driven OFF / ON branches are honoured.
* INNER gate — cadence ``is_tick_due(now, last_tick_at, interval_seconds)``.

Both gates are exercised against a real :class:`WorkerState` + write_state_fn
so the persistence side of the contract is also pinned.

F1/F2-clean — every seam is the public deps kwarg; no monkey-patching.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from kairix.core.protocols import EntitySummaryProjectionResult
from kairix.knowledge.entities.summary_projector import EntitySummaryProjectorDeps
from kairix.worker import maybe_run_entity_summary_projector_tick
from kairix.worker_state import WorkerPhase, WorkerState
from tests.fakes import FakeEntitySummaryProjector, FakeFeatureFlagResolver

pytestmark = pytest.mark.unit


def _new_state() -> WorkerState:
    return WorkerState()


def _silent_write(_state: WorkerState, _path: Path) -> None:
    """Capture-only write — tests assert on state directly."""


def _build_deps(
    *,
    flag_on: bool,
    result: EntitySummaryProjectionResult | None = None,
) -> tuple[EntitySummaryProjectorDeps, FakeEntitySummaryProjector]:
    """Build deps with a FakeEntitySummaryProjector behind a pinned flag."""
    projector = FakeEntitySummaryProjector(result=result if result is not None else EntitySummaryProjectionResult())
    resolver = (
        FakeFeatureFlagResolver().with_flag("entity_summary_indexing_enabled", True)
        if flag_on
        else FakeFeatureFlagResolver().with_flag("entity_summary_indexing_enabled", False)
    )
    deps = EntitySummaryProjectorDeps(
        flag_reader=lambda: resolver.get("entity_summary_indexing_enabled"),
        projector_factory=lambda: projector,  # type: ignore[arg-type] — FakeEntitySummaryProjector satisfies the Protocol via duck-typing; mypy doesn't see the runtime_checkable check
    )
    return deps, projector


def _transitions() -> tuple[list[WorkerPhase], Any]:
    """Capture phase transitions so tests can assert MAINTENANCE→IDLE."""
    captured: list[WorkerPhase] = []

    def _transition(phase: WorkerPhase) -> None:
        captured.append(phase)

    return captured, _transition


def test_flag_off_returns_unchanged_last_tick(tmp_path: Path) -> None:
    """OFF branch — the dispatcher returns ``None`` and the wrapper
    leaves ``last_tick_at`` unchanged so the next OFF→ON flip fires
    immediately rather than waiting an interval.

    Sabotage-proof: drop the ``if result is None: return last_tick_at``
    branch and the wrapper advances the timestamp even when nothing
    actually ran.
    """
    deps, projector = _build_deps(flag_on=False)
    transitions, transition = _transitions()
    state = _new_state()

    new_tick = maybe_run_entity_summary_projector_tick(
        deps=deps,
        transition=transition,
        state=state,
        state_path=tmp_path / "worker.state.json",
        write_state_fn=_silent_write,
        now=100.0,
        last_tick_at=0.0,
        interval_seconds=60,
    )
    assert new_tick == 0.0
    assert projector.ticks == []
    # OFF still passes through MAINTENANCE→IDLE phases on the cadence
    # check; only the projector itself is skipped.
    assert transitions == [WorkerPhase.MAINTENANCE, WorkerPhase.IDLE]


def test_flag_on_within_interval_skips_tick(tmp_path: Path) -> None:
    """INNER cadence gate — within the interval window the wrapper
    returns ``last_tick_at`` unchanged without invoking the projector."""
    deps, projector = _build_deps(flag_on=True)
    transitions, transition = _transitions()
    state = _new_state()
    new_tick = maybe_run_entity_summary_projector_tick(
        deps=deps,
        transition=transition,
        state=state,
        state_path=tmp_path / "worker.state.json",
        write_state_fn=_silent_write,
        now=10.0,
        last_tick_at=5.0,  # 5s ago, well inside the 60s interval
        interval_seconds=60,
    )
    assert new_tick == 5.0
    assert projector.ticks == []
    # Cadence gate fires before MAINTENANCE — no phase transitions happen.
    assert transitions == []


def test_flag_on_due_for_tick_runs_and_records_counters(tmp_path: Path) -> None:
    """Productive tick — flag ON + cadence due → projector runs, the
    counters land on WorkerState, and ``last_tick_at`` advances.

    Sabotage-proof: drop the
    ``state.last_entity_summary_projected = int(...)`` line and the
    assertion below catches.
    """
    result = EntitySummaryProjectionResult(projected=5, updated=1, skipped=2, failed=0)
    deps, projector = _build_deps(flag_on=True, result=result)
    transitions, transition = _transitions()
    state = _new_state()
    new_tick = maybe_run_entity_summary_projector_tick(
        deps=deps,
        transition=transition,
        state=state,
        state_path=tmp_path / "worker.state.json",
        write_state_fn=_silent_write,
        now=200.0,
        last_tick_at=100.0,
        interval_seconds=60,
    )
    assert new_tick == 200.0
    assert projector.ticks == [200]  # default per_tick_max_items
    assert state.last_entity_summary_tick_at == 200.0
    assert state.last_entity_summary_projected == 5
    assert state.last_entity_summary_updated == 1
    assert state.last_entity_summary_skipped == 2
    assert state.last_entity_summary_failed == 0
    assert transitions == [WorkerPhase.MAINTENANCE, WorkerPhase.IDLE]


def test_write_state_fn_called_with_persisted_state(tmp_path: Path) -> None:
    """Each productive tick passes the mutated state to the write
    callback so the cadence + counters survive a worker restart."""
    captured_state: list[WorkerState] = []

    def _capturing_write(state: WorkerState, _path: Path) -> None:
        captured_state.append(state)

    result = EntitySummaryProjectionResult(projected=3, failed=1)
    deps, _ = _build_deps(flag_on=True, result=result)
    _, transition = _transitions()
    state = _new_state()

    maybe_run_entity_summary_projector_tick(
        deps=deps,
        transition=transition,
        state=state,
        state_path=tmp_path / "worker.state.json",
        write_state_fn=_capturing_write,
        now=300.0,
        last_tick_at=200.0,
        interval_seconds=60,
    )
    assert len(captured_state) == 1
    assert captured_state[0].last_entity_summary_projected == 3
    assert captured_state[0].last_entity_summary_failed == 1


def test_first_run_with_zero_last_tick_fires_immediately(tmp_path: Path) -> None:
    """First post-flag-flip / restart — ``last_tick_at == 0.0`` ALWAYS
    counts as due regardless of ``interval_seconds``. Locks the
    cutover-day contract: an operator flipping the flag ON sees the
    projector start within the next worker loop iteration, not an
    interval window later."""
    deps, projector = _build_deps(flag_on=True, result=EntitySummaryProjectionResult(projected=1))
    _, transition = _transitions()
    state = _new_state()

    new_tick = maybe_run_entity_summary_projector_tick(
        deps=deps,
        transition=transition,
        state=state,
        state_path=tmp_path / "worker.state.json",
        write_state_fn=_silent_write,
        now=50.0,
        last_tick_at=0.0,
        interval_seconds=3600,  # huge interval — but is_tick_due still fires on first run
    )
    assert new_tick == 50.0
    assert projector.ticks == [200]
