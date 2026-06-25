"""Unit: the worker re-chunk sweep tick gate (ADR-028 Wave F.4).

``run_rechunk_sweep_tick`` runs the injected ``rechunk_sweep_fn`` only when
BOTH ``re_chunk_sweep_enabled`` AND ``chunker_registry_dispatch_enabled`` are
ON, and never lets a sweep failure escape. Exercised through the WorkerDeps
seams (flag_reader + rechunk_sweep_fn) — no monkeypatch (F1).
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from kairix.worker import WorkerDeps, run_rechunk_sweep_tick

pytestmark = pytest.mark.unit


def _flags(**vals: bool) -> Callable[[str], bool]:
    return lambda name: vals.get(name, False)


def test_noop_when_sweep_flag_off() -> None:
    calls: list[int] = []
    deps = WorkerDeps(
        flag_reader=_flags(re_chunk_sweep_enabled=False, chunker_registry_dispatch_enabled=True),
        rechunk_sweep_fn=lambda: calls.append(1),
    )
    run_rechunk_sweep_tick(deps)
    assert calls == [], "sweep must not run when re_chunk_sweep_enabled is OFF"


def test_noop_when_registry_dispatch_flag_off() -> None:
    calls: list[int] = []
    deps = WorkerDeps(
        flag_reader=_flags(re_chunk_sweep_enabled=True, chunker_registry_dispatch_enabled=False),
        rechunk_sweep_fn=lambda: calls.append(1),
    )
    run_rechunk_sweep_tick(deps)
    assert calls == [], "sweep must no-op when chunker_registry_dispatch_enabled is OFF (would churn)"


def test_runs_when_both_flags_on() -> None:
    calls: list[int] = []
    deps = WorkerDeps(
        flag_reader=_flags(re_chunk_sweep_enabled=True, chunker_registry_dispatch_enabled=True),
        rechunk_sweep_fn=lambda: calls.append(1),
    )
    run_rechunk_sweep_tick(deps)
    assert calls == [1], "sweep runs when both flags are ON"


def test_swallows_sweep_failure() -> None:
    def boom() -> None:
        raise RuntimeError("sweep blew up")

    deps = WorkerDeps(
        flag_reader=_flags(re_chunk_sweep_enabled=True, chunker_registry_dispatch_enabled=True),
        rechunk_sweep_fn=boom,
    )
    # Must not propagate — a maintenance failure can't take the worker down.
    run_rechunk_sweep_tick(deps)
