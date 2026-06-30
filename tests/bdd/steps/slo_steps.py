"""Step definitions for cli_slo.feature (PLA-256).

F46-clean: every scenario composes through the public CLI surface
(``kairix.quality.probe.slo_cli.main``) in its default synthetic mode — no
direct pipeline construction, no monkeypatching (F1), no env vars (F2).
F13-clean: scenarios speak in engineer/operator language, never
implementation symbols.
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from dataclasses import dataclass, field
from typing import Any

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.quality.probe.slo_cli import main as slo_main

pytestmark = pytest.mark.bdd


@dataclass
class _SloState:
    """Per-scenario state — fresh on every scenario."""

    exit_code: int = 0
    report: dict[str, Any] = field(default_factory=dict)


@pytest.fixture
def _slo_state() -> _SloState:
    return _SloState()


def _run_slo(state: _SloState, argv: list[str]) -> None:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        state.exit_code = slo_main(argv)
    state.report = json.loads(buffer.getvalue())


@given("the synthetic measurement workload")
def _given_synthetic_workload(_slo_state: _SloState) -> None:
    # The default mode seeds the synthetic corpus in-process; nothing to set up.
    assert _slo_state.exit_code == 0


@when("the engineer runs the SLO harness as JSON")
def _when_run_json(_slo_state: _SloState) -> None:
    _run_slo(_slo_state, ["--mode", "synthetic", "--format", "json"])


@when(parsers.parse("the engineer runs the SLO harness at concurrency {n:d} as JSON"))
def _when_run_at_concurrency(_slo_state: _SloState, n: int) -> None:
    _run_slo(_slo_state, ["--mode", "synthetic", "--concurrency", str(n), "--format", "json"])


@then("the report includes cold and warm latency for every most-used command")
def _then_latency_covers_commands(_slo_state: _SloState) -> None:
    assert _slo_state.exit_code == 0
    latency = _slo_state.report["latency"]
    commands = {row["command"] for row in latency}
    assert commands == {"brief", "remember", "recall", "search"}
    phases = {row["phase"] for row in latency}
    assert phases == {"cold", "warm"}


@then("the report includes fact-recall quality for the labelled suite")
def _then_recall_reported(_slo_state: _SloState) -> None:
    recall = _slo_state.report["recall"]
    assert recall, "expected at least one recall suite row"
    assert recall[0]["recall_at_k"] == 1.0
    assert recall[0]["n_facts"] > 0


@then("every agent-facing result carries a resolvable source breadcrumb")
def _then_affordance_complete(_slo_state: _SloState) -> None:
    affordance = _slo_state.report["affordance"]
    assert affordance, "expected affordance rows"
    assert all(row["pct_resolvable"] == 100.0 for row in affordance)


@then(parsers.parse("the latency table covers concurrency {low:d} and concurrency {high:d}"))
def _then_latency_concurrency_levels(_slo_state: _SloState, low: int, high: int) -> None:
    assert _slo_state.exit_code == 0
    levels = {row["concurrency"] for row in _slo_state.report["latency"]}
    assert low in levels
    assert high in levels
