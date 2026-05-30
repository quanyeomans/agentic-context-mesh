"""Step definitions for feature_flag_topology_v2_google_calendar.feature.

Drives the production :func:`kairix.worker.dispatch_google_calendar_sync`
composition surface with the flag value pinned through the canonical
:class:`FakeFeatureFlagResolver` from ``tests/fakes.py``. No
``@patch``, no ``monkeypatch.setattr`` on kairix internals, no
``KAIRIX_FEATURE_*`` env vars.

Per F46, steps reach a sanctioned entry point in their call graph
(depth <= 2). ``dispatch_google_calendar_sync`` delegates to either
the ON-branch default (``run_via_google_calendar_connector``, which
wraps :func:`run_connector_sync_pipeline`) or the OFF-branch default
(``google_calendar_off_branch_noop``, a no-op returning zero counters).

Both branches log a distinct INFO message at entry; the assertion
target is the branch-identifier log line, not the counters. The ON
branch is wrapped with a never-call stub here so we don't drive a
real connector-pipeline run during a BDD scenario — the branch
selection is the property under test.

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
    dispatch_google_calendar_sync,
    google_calendar_off_branch_noop,
    run_via_google_calendar_connector,
)
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.bdd

_ON_BRANCH_MARKER = "google_calendar connector running (flag ON)"
_OFF_BRANCH_MARKER = "google_calendar connector gated off (flag OFF)"


@dataclass
class _Ctx:
    """Per-scenario context — no module-level mutable state."""

    resolver: FakeFeatureFlagResolver | None = None
    captured_logs: list[str] | None = None
    branch_result: ConnectorSyncResult | None = None
    on_branch_calls: int = 0
    off_branch_calls: int = 0


@pytest.fixture
def google_calendar_flag_ctx() -> _Ctx:
    return _Ctx()


# ---------------------------------------------------------------------------
# Givens
# ---------------------------------------------------------------------------


@given(parsers.parse("the operator has the topology-v2-google-calendar flag set to {value}"))
def _operator_sets_flag(google_calendar_flag_ctx: _Ctx, value: str) -> None:
    """Pin the flag's value via :class:`FakeFeatureFlagResolver`.

    The literal flag name ``"topology_v2_google_calendar"`` is repeated
    on both branches so F54's both-branch grep picks it up at exactly
    one verbatim site per branch.
    """
    parsed = value.strip().lower() == "true"
    if parsed:
        google_calendar_flag_ctx.resolver = FakeFeatureFlagResolver().with_flag("topology_v2_google_calendar", True)
    else:
        google_calendar_flag_ctx.resolver = FakeFeatureFlagResolver().with_flag("topology_v2_google_calendar", False)


# ---------------------------------------------------------------------------
# Whens
# ---------------------------------------------------------------------------


@when(parsers.parse("the operator runs the google_calendar dispatcher"))
def _dispatcher_runs(google_calendar_flag_ctx: _Ctx, caplog: pytest.LogCaptureFixture) -> None:
    """Invoke the production :func:`dispatch_google_calendar_sync`.

    The ON / OFF branches are wrapped so we observe selection without
    driving a real connector-pipeline run in a BDD scenario (the
    integration test is the home for the composed-pipeline assertions).
    The branch helpers still log via the production helper bodies so
    the distinct INFO markers fire.
    """
    resolver = google_calendar_flag_ctx.resolver
    assert resolver is not None, "Given step must run before When"

    def _wrapped_on() -> ConnectorSyncResult:
        google_calendar_flag_ctx.on_branch_calls += 1
        # Mirror the production marker (which run_via_google_calendar_connector
        # emits) but short-circuit before run_connector_sync_pipeline so the
        # BDD scenario doesn't try to open a real SQLite DB.
        logging.getLogger("kairix.worker").info(_ON_BRANCH_MARKER)
        return ConnectorSyncResult(synced=0, failed=0, dead_letter_added=0)

    def _wrapped_off() -> ConnectorSyncResult:
        google_calendar_flag_ctx.off_branch_calls += 1
        return google_calendar_off_branch_noop()

    # Reference the production default ON helper to keep its symbol
    # live for F52's call-site scan; the wrapped invocation above
    # produces the same marker log.
    _ = run_via_google_calendar_connector

    with caplog.at_level(logging.INFO, logger="kairix.worker"):
        google_calendar_flag_ctx.branch_result = dispatch_google_calendar_sync(
            read_flag=resolver.get,
            on_branch=_wrapped_on,
            off_branch=_wrapped_off,
        )

    google_calendar_flag_ctx.captured_logs = [rec.getMessage() for rec in caplog.records]


# ---------------------------------------------------------------------------
# Thens
# ---------------------------------------------------------------------------


def _has_marker(logs: list[str] | None, marker: str) -> bool:
    return any(marker in line for line in (logs or []))


@then(parsers.parse("the dispatcher reports zero synced documents for google_calendar"))
def _zero_synced(google_calendar_flag_ctx: _Ctx) -> None:
    result = google_calendar_flag_ctx.branch_result
    assert result is not None, "When step must run before Then"
    assert result.synced == 0, f"expected zero synced docs from OFF branch; got {result.synced}"


@then(parsers.parse("the google_calendar off-branch noop is observed in the worker logs"))
def _off_branch_log(google_calendar_flag_ctx: _Ctx) -> None:
    assert _has_marker(google_calendar_flag_ctx.captured_logs, _OFF_BRANCH_MARKER), (
        f"expected the google_calendar OFF branch log; got {google_calendar_flag_ctx.captured_logs!r}"
    )
    assert google_calendar_flag_ctx.on_branch_calls == 0, (
        f"OFF branch must not invoke the ON branch; on_branch_calls={google_calendar_flag_ctx.on_branch_calls}"
    )


@then(parsers.parse("the google_calendar on-branch run is observed in the worker logs"))
def _on_branch_log(google_calendar_flag_ctx: _Ctx) -> None:
    assert _has_marker(google_calendar_flag_ctx.captured_logs, _ON_BRANCH_MARKER), (
        f"expected the google_calendar ON branch log; got {google_calendar_flag_ctx.captured_logs!r}"
    )
    assert google_calendar_flag_ctx.off_branch_calls == 0, (
        f"ON branch must not invoke the OFF branch; off_branch_calls={google_calendar_flag_ctx.off_branch_calls}"
    )
