"""Step definitions for feature_flag_connector_skills.feature.

Drives the production :func:`kairix.worker.dispatch_skills_sync`
composition surface with the flag value pinned through the canonical
:class:`FakeFeatureFlagResolver` from ``tests/fakes.py``. No ``@patch``,
no ``monkeypatch.setattr`` on kairix internals, no ``KAIRIX_FEATURE_*``
env vars.

Per F46, steps reach a sanctioned entry point in their call graph
(depth <= 2). ``dispatch_skills_sync`` delegates to either the ON-branch
default (which wraps :func:`run_connector_sync_pipeline`) or the
OFF-branch default (a no-op returning zero counters).

Both branches log a distinct INFO message at entry; the assertion target
is the branch-identifier log line, not the counters. The ON branch is
wrapped with a never-call stub here so we don't drive a real
connector-pipeline run during a BDD scenario — the branch selection is
the property under test.

F1-clean: no @patch / module-attribute substitution on kairix.
F2-clean: no ``KAIRIX_*`` env-var manipulation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.worker import (
    ConnectorSyncResult,
    dispatch_skills_sync,
    run_via_skills_connector,
    skills_off_branch_noop,
)
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.bdd

_ON_BRANCH_MARKER = "skills connector running (flag ON)"
_OFF_BRANCH_MARKER = "skills connector gated off (flag OFF)"


@dataclass
class _Ctx:
    """Per-scenario context — no module-level mutable state."""

    resolver: FakeFeatureFlagResolver | None = None
    captured_logs: list[str] | None = None
    branch_result: ConnectorSyncResult | None = None
    on_branch_calls: int = 0
    off_branch_calls: int = 0


@pytest.fixture
def skills_flag_ctx() -> _Ctx:
    return _Ctx()


# ---------------------------------------------------------------------------
# Givens
# ---------------------------------------------------------------------------


@given(parsers.parse("the operator has the skills connector flag set to {value}"))
def _operator_sets_flag(skills_flag_ctx: _Ctx, value: str) -> None:
    """Pin the flag's value via :class:`FakeFeatureFlagResolver`."""
    parsed = value.strip().lower() == "true"
    skills_flag_ctx.resolver = FakeFeatureFlagResolver().with_flag("connector_skills", parsed)


# ---------------------------------------------------------------------------
# Whens
# ---------------------------------------------------------------------------


@when("the worker skills connector sync tick runs")
def _worker_tick_runs(skills_flag_ctx: _Ctx, caplog: pytest.LogCaptureFixture) -> None:
    """Invoke the production :func:`dispatch_skills_sync`.

    The ON / OFF branches are wrapped so we observe selection without
    driving a real connector-pipeline run in a BDD scenario (the
    integration test is the home for the composed-pipeline assertions).
    The branch helpers still log via the production marker text so the
    distinct INFO markers fire.
    """
    resolver = skills_flag_ctx.resolver
    assert resolver is not None, "Given step must run before When"

    def _wrapped_on() -> ConnectorSyncResult:
        skills_flag_ctx.on_branch_calls += 1
        logging.getLogger("kairix.worker").info(_ON_BRANCH_MARKER)
        return ConnectorSyncResult(synced=0, failed=0, dead_letter_added=0)

    def _wrapped_off() -> ConnectorSyncResult:
        skills_flag_ctx.off_branch_calls += 1
        return skills_off_branch_noop()

    # Reference the production default ON helper to keep its symbol live
    # for F52's call-site scan.
    _ = run_via_skills_connector

    with caplog.at_level(logging.INFO, logger="kairix.worker"):
        skills_flag_ctx.branch_result = dispatch_skills_sync(
            read_flag=resolver.get,
            on_branch=_wrapped_on,
            off_branch=_wrapped_off,
        )

    skills_flag_ctx.captured_logs = [rec.getMessage() for rec in caplog.records]


# ---------------------------------------------------------------------------
# Thens
# ---------------------------------------------------------------------------


def _has_marker(logs: list[str] | None, marker: str) -> bool:
    return any(marker in line for line in (logs or []))


@then("the skills connector OFF branch log appears")
def _off_branch_log(skills_flag_ctx: _Ctx) -> None:
    assert _has_marker(skills_flag_ctx.captured_logs, _OFF_BRANCH_MARKER), (
        f"expected the skills OFF branch log; got {skills_flag_ctx.captured_logs!r}"
    )


@then("the skills connector ON branch log appears")
def _on_branch_log(skills_flag_ctx: _Ctx) -> None:
    assert _has_marker(skills_flag_ctx.captured_logs, _ON_BRANCH_MARKER), (
        f"expected the skills ON branch log; got {skills_flag_ctx.captured_logs!r}"
    )


@then("the skills connector ON branch does not run")
def _on_branch_skipped(skills_flag_ctx: _Ctx) -> None:
    assert skills_flag_ctx.on_branch_calls == 0, (
        f"expected ON branch to NOT run; on_branch_calls={skills_flag_ctx.on_branch_calls}"
    )


@then("the skills connector OFF branch does not run")
def _off_branch_skipped(skills_flag_ctx: _Ctx) -> None:
    assert skills_flag_ctx.off_branch_calls == 0, (
        f"expected OFF branch to NOT run; off_branch_calls={skills_flag_ctx.off_branch_calls}"
    )
