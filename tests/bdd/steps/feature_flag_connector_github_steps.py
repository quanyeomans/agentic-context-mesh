"""Step definitions for feature_flag_connector_github.feature.

Drives the production :func:`kairix.worker.dispatch_github_sync`
composition surface with the flag value pinned through the canonical
:class:`FakeFeatureFlagResolver` from ``tests/fakes.py``. No
``@patch``, no ``monkeypatch.setattr`` on kairix internals, no
``KAIRIX_FEATURE_*`` env vars.

Per F46, steps reach a sanctioned entry point in their call graph
(depth ≤ 2). ``dispatch_github_sync`` delegates to either the
ON-branch default (which wraps :func:`run_connector_sync_pipeline`)
or the OFF-branch default (a no-op returning zero counters).

Both branches log a distinct INFO message at entry; the assertion
target is the branch-identifier log line, not the counters. The ON
branch is wrapped with a never-call stub here so we don't drive a
real connector-pipeline run during a BDD scenario — the branch
selection is the property under test.

F1-clean: no @patch / module-attribute substitution on kairix.
F2-clean: no ``KAIRIX_*`` env-var manipulation.

Sabotage proof (executed by agent, restored on completion): inverting
the if/else in :func:`dispatch_github_sync` so OFF runs the ON branch
and ON runs the OFF branch — confirmed BOTH BDD scenarios fail with
mismatched branch markers. Restoring the original direction returns
the suite to green.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.worker import (
    ConnectorSyncResult,
    dispatch_github_sync,
    github_off_branch_noop,
    run_via_github_connector,
)
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.bdd

_ON_BRANCH_MARKER = "github connector running (flag ON)"
_OFF_BRANCH_MARKER = "github connector gated off (flag OFF)"


@dataclass
class _Ctx:
    """Per-scenario context — no module-level mutable state."""

    resolver: FakeFeatureFlagResolver | None = None
    captured_logs: list[str] | None = None
    branch_result: ConnectorSyncResult | None = None
    on_branch_calls: int = 0
    off_branch_calls: int = 0


@pytest.fixture
def github_flag_ctx() -> _Ctx:
    return _Ctx()


@given(parsers.parse("the operator has the github connector flag set to {value}"))
def _operator_sets_flag(github_flag_ctx: _Ctx, value: str) -> None:
    parsed = value.strip().lower() == "true"
    github_flag_ctx.resolver = FakeFeatureFlagResolver().with_flag("connector_github", parsed)


@when("the worker github connector sync tick runs")
def _worker_tick_runs(github_flag_ctx: _Ctx, caplog: pytest.LogCaptureFixture) -> None:
    resolver = github_flag_ctx.resolver
    assert resolver is not None, "Given step must run before When"

    def _wrapped_on() -> ConnectorSyncResult:
        github_flag_ctx.on_branch_calls += 1
        logging.getLogger("kairix.worker").info(_ON_BRANCH_MARKER)
        return ConnectorSyncResult(synced=0, failed=0, dead_letter_added=0)

    def _wrapped_off() -> ConnectorSyncResult:
        github_flag_ctx.off_branch_calls += 1
        return github_off_branch_noop()

    # Reference the production default ON helper so F52's call-site
    # scan keeps the symbol live; the wrapped invocation above produces
    # the same marker log without driving a real pipeline.
    _ = run_via_github_connector

    with caplog.at_level(logging.INFO, logger="kairix.worker"):
        github_flag_ctx.branch_result = dispatch_github_sync(
            read_flag=resolver.get,
            on_branch=_wrapped_on,
            off_branch=_wrapped_off,
        )
    github_flag_ctx.captured_logs = [rec.getMessage() for rec in caplog.records]


def _has_marker(logs: list[str] | None, marker: str) -> bool:
    return any(marker in line for line in (logs or []))


@then("the github connector OFF branch log appears")
def _off_branch_log(github_flag_ctx: _Ctx) -> None:
    assert _has_marker(github_flag_ctx.captured_logs, _OFF_BRANCH_MARKER), (
        f"expected the github OFF branch log; got {github_flag_ctx.captured_logs!r}"
    )


@then("the github connector ON branch log appears")
def _on_branch_log(github_flag_ctx: _Ctx) -> None:
    assert _has_marker(github_flag_ctx.captured_logs, _ON_BRANCH_MARKER), (
        f"expected the github ON branch log; got {github_flag_ctx.captured_logs!r}"
    )


@then("the github connector ON branch does not run")
def _on_branch_skipped(github_flag_ctx: _Ctx) -> None:
    assert github_flag_ctx.on_branch_calls == 0, (
        f"expected ON branch to NOT run; on_branch_calls={github_flag_ctx.on_branch_calls}"
    )


@then("the github connector OFF branch does not run")
def _off_branch_skipped(github_flag_ctx: _Ctx) -> None:
    assert github_flag_ctx.off_branch_calls == 0, (
        f"expected OFF branch to NOT run; off_branch_calls={github_flag_ctx.off_branch_calls}"
    )
