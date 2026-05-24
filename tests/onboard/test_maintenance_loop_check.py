"""KFEAT-021 Phase 1 — unit tests for ``check_maintenance_loop_ticking``.

Covers the four branches the brief describes:

  1. **flag OFF** → ok=True, "skipped — flag off (default-safe)" detail.
  2. **flag ON, never ticked** → ok=False with the "no tick yet"
     remediation pointing at ``kairix worker maintenance``.
  3. **flag ON, within jitter window** → ok=True with cadence delta.
  4. **flag ON, past jitter window** → ok=False with the stalled
     remediation.

Test discipline:
  * F1 / F2 clean — every branch is exercised via
    :class:`MaintenanceLoopCheckDeps` injection (no monkey-patch, no
    setenv).
  * F8 — every test carries ``@pytest.mark.unit``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from kairix.platform.onboard.check import (
    MaintenanceLoopCheckDeps,
    check_maintenance_loop_ticking,
)

pytestmark = pytest.mark.unit


@dataclass
class _FakeWorkerState:
    """Minimal stand-in for ``WorkerState`` — only the fields the check reads."""

    last_maintenance_tick_at: float = 0.0


def _deps(
    *,
    flag_on: bool,
    state: Any | None,
    interval: int = 86400,
    now: float = 1_716_336_000.0,
) -> MaintenanceLoopCheckDeps:
    """Build a deps with deterministic flag / state / clock substitutes."""
    return MaintenanceLoopCheckDeps(
        flag_reader=lambda _name: flag_on,
        state_reader=lambda: state,
        interval_reader=lambda: interval,
        clock=lambda: now,
    )


def test_flag_off_returns_skipped_ok() -> None:
    """OFF branch: skipped, default-safe."""
    result = check_maintenance_loop_ticking(_deps(flag_on=False, state=None))
    assert result.ok is True
    assert "skipped" in result.detail
    assert "maintenance_loop" in result.detail


def test_flag_on_but_never_ticked_returns_failure() -> None:
    """No prior tick → ok=False with the on-demand verb in the fix."""
    result = check_maintenance_loop_ticking(_deps(flag_on=True, state=_FakeWorkerState(last_maintenance_tick_at=0.0)))
    assert result.ok is False
    assert "no tick" in result.detail.lower()
    assert result.fix is not None
    assert "kairix worker maintenance" in result.fix


def test_flag_on_within_jitter_window_returns_ok() -> None:
    """Recent tick within interval * 1.5 → ok=True with cadence detail."""
    now = 1_716_400_000.0
    last = now - 3_600  # 1 hour ago
    result = check_maintenance_loop_ticking(
        _deps(
            flag_on=True,
            state=_FakeWorkerState(last_maintenance_tick_at=last),
            interval=86400,  # 24 h
            now=now,
        )
    )
    assert result.ok is True
    assert "within jitter window" in result.detail
    assert "3600s ago" in result.detail


def test_flag_on_past_jitter_window_returns_failure() -> None:
    """Stalled loop → ok=False with the stalled-loop fix."""
    now = 1_716_400_000.0
    last = now - (86400 * 2)  # 2 days ago, interval = 1 day, cap = 1.5d
    result = check_maintenance_loop_ticking(
        _deps(
            flag_on=True,
            state=_FakeWorkerState(last_maintenance_tick_at=last),
            interval=86400,
            now=now,
        )
    )
    assert result.ok is False
    assert "stalled" in result.detail
    assert result.fix is not None
    assert "kairix worker status" in result.fix


def test_flag_on_state_none_falls_through_to_never_ticked() -> None:
    """``state_reader`` returning None == never ticked."""
    result = check_maintenance_loop_ticking(_deps(flag_on=True, state=None))
    assert result.ok is False
    assert "no tick" in result.detail.lower()


def test_deps_default_factory_does_not_explode_at_construction() -> None:
    """The Deps default factories must not trigger side effects at import time."""
    deps = MaintenanceLoopCheckDeps()
    assert callable(deps.flag_reader)
    assert callable(deps.state_reader)
    assert callable(deps.interval_reader)
    assert callable(deps.clock)


def test_default_interval_reader_returns_positive_int() -> None:
    """The default factory wires a real interval reader that returns a positive int.

    Exercised through the public Deps default — calling the bound
    reader checks it produces a sane value without reaching into the
    underscore-prefixed helper directly.
    """
    deps = MaintenanceLoopCheckDeps()
    value = deps.interval_reader()
    assert isinstance(value, int)
    assert value > 0


def test_default_clock_returns_current_wall_time() -> None:
    """The default factory wires a real clock that returns the current wall time.

    Exercises the ``_default_clock`` production seam through the
    public Deps default. Calling the bound clock should return a
    monotonically-recent epoch second (within a 5-second tolerance to
    survive any test-suite parallelism noise).
    """
    import time as _time

    deps = MaintenanceLoopCheckDeps()
    before = _time.time()
    value = deps.clock()
    after = _time.time()
    assert isinstance(value, float)
    assert before - 5 <= value <= after + 5


def test_default_state_reader_returns_none_or_worker_state() -> None:
    """The default factory wires a real state reader that returns ``WorkerState | None``.

    Exercises the ``_default_worker_state_reader`` production seam
    through the public Deps default. In a fresh test environment the
    worker state file does not exist, so the reader returns ``None``;
    when it exists (live VM), it returns a ``WorkerState``-shaped object
    (we test only the type, not the contents).
    """
    from kairix.worker_state import WorkerState

    deps = MaintenanceLoopCheckDeps()
    value = deps.state_reader()
    assert value is None or isinstance(value, WorkerState)
