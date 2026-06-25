"""Step definitions for feature_flag_re_chunk_sweep_enabled.feature (ADR-028).

OFF branch: ``run_rechunk_sweep_tick`` is a no-op — the injected
``rechunk_sweep_fn`` is never called. ON branch: with both
``re_chunk_sweep_enabled`` AND ``chunker_registry_dispatch_enabled`` ON, the
tick runs and invokes the sweep.

F1-clean: the flag resolver + sweep function are injected through the WorkerDeps
seam (no @patch / module-attribute substitution). F2-clean: flag state comes
from :class:`FakeFeatureFlagResolver`, never a ``KAIRIX_*`` env var.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from pytest_bdd import given, then, when

from kairix.worker import WorkerDeps, run_rechunk_sweep_tick
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.bdd

_SWEEP_FLAG = "re_chunk_sweep_enabled"
_REGISTRY_FLAG = "chunker_registry_dispatch_enabled"


@dataclass
class _State:
    deps: WorkerDeps | None = None
    calls: list[int] = field(default_factory=list)


@pytest.fixture
def rechunk_sweep_flag_state() -> _State:
    return _State()


def _deps(state: _State, *, sweep_on: bool) -> WorkerDeps:
    resolver = (
        FakeFeatureFlagResolver().with_flag(_SWEEP_FLAG, sweep_on).with_flag(_REGISTRY_FLAG, True)
    )
    return WorkerDeps(flag_reader=resolver.get, rechunk_sweep_fn=lambda: state.calls.append(1))


@given("a worker re-chunk sweep maintenance tick")
def _given_tick(rechunk_sweep_flag_state: _State) -> None:
    assert rechunk_sweep_flag_state.calls == []


@given("the re_chunk_sweep_enabled flag is OFF")
def _flag_off(rechunk_sweep_flag_state: _State) -> None:
    rechunk_sweep_flag_state.deps = _deps(rechunk_sweep_flag_state, sweep_on=False)


@given("the re_chunk_sweep_enabled flag is ON")
def _flag_on(rechunk_sweep_flag_state: _State) -> None:
    rechunk_sweep_flag_state.deps = _deps(rechunk_sweep_flag_state, sweep_on=True)


@when("the maintenance tick fires")
def _fire(rechunk_sweep_flag_state: _State) -> None:
    assert rechunk_sweep_flag_state.deps is not None
    run_rechunk_sweep_tick(rechunk_sweep_flag_state.deps)


@then("the re-chunk sweep does not run")
def _then_skipped(rechunk_sweep_flag_state: _State) -> None:
    assert rechunk_sweep_flag_state.calls == []


@then("the re-chunk sweep runs")
def _then_ran(rechunk_sweep_flag_state: _State) -> None:
    assert rechunk_sweep_flag_state.calls == [1]


__all__ = ["rechunk_sweep_flag_state"]
