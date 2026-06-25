"""F54 integration parity for the re_chunk_sweep_enabled flag (ADR-028 Wave F.4).

Pins the flag's two branches through the production tick entry point
(``worker.run_rechunk_sweep_tick``): OFF skips the sweep entirely; ON (with
chunker_registry_dispatch_enabled also ON) runs it. Flag resolution uses
:class:`FakeFeatureFlagResolver` and the sweep fn is injected through the
WorkerDeps seam (F1-clean: no @patch / module-attribute substitution).
"""

from __future__ import annotations

import pytest

from kairix.worker import WorkerDeps, run_rechunk_sweep_tick
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.integration


def test_flag_off_skips_the_sweep() -> None:
    calls: list[int] = []
    resolver = (
        FakeFeatureFlagResolver()
        .with_flag("re_chunk_sweep_enabled", False)
        .with_flag("chunker_registry_dispatch_enabled", True)
    )
    run_rechunk_sweep_tick(WorkerDeps(flag_reader=resolver.get, rechunk_sweep_fn=lambda: calls.append(1)))
    assert calls == []


def test_flag_on_runs_the_sweep() -> None:
    calls: list[int] = []
    resolver = (
        FakeFeatureFlagResolver()
        .with_flag("re_chunk_sweep_enabled", True)
        .with_flag("chunker_registry_dispatch_enabled", True)
    )
    run_rechunk_sweep_tick(WorkerDeps(flag_reader=resolver.get, rechunk_sweep_fn=lambda: calls.append(1)))
    assert calls == [1]


def test_sweep_on_but_registry_off_is_a_noop() -> None:
    calls: list[int] = []
    resolver = (
        FakeFeatureFlagResolver()
        .with_flag("re_chunk_sweep_enabled", True)
        .with_flag("chunker_registry_dispatch_enabled", False)
    )
    run_rechunk_sweep_tick(WorkerDeps(flag_reader=resolver.get, rechunk_sweep_fn=lambda: calls.append(1)))
    assert calls == [], "the sweep must not run while ingest still uses the legacy chunker"
