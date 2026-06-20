"""Integration tests for the ``connector_skills`` flag (F54).

Exercises both branches of :func:`kairix.worker.dispatch_skills_sync`
through the production composition surface:

  * **Flag OFF** — the connector slot is a no-op; the off-branch helper
    returns zero counters and emits the OFF-branch INFO log. The ON
    branch is wrapped with a never-call stub; a misroute increments its
    counter and the assertion fails loudly.
  * **Flag ON** — the ON branch fires; the OFF branch is wrapped with a
    never-call stub. The ON branch's marker INFO log appears.

F47 — both branches are reached via the production
``dispatch_skills_sync`` entry point; no direct ``*Pipeline(...)``
construction in this file.

F1-clean: ``FakeFeatureFlagResolver`` from ``tests/fakes.py`` is threaded
through the production ``dispatch_skills_sync(read_flag=...)`` DI seam —
no @patch / module-attribute substitution on kairix.

Sabotage proof (executed by the agent, restored on completion):
inverting the if/else in :func:`dispatch_skills_sync` so OFF runs the ON
branch and ON runs the OFF branch — confirmed that BOTH
:func:`test_skills_flag_off_branch_runs` AND
:func:`test_skills_flag_on_branch_runs` fail. Restoring the original
branch direction returns both tests to green. (See Step 2.6 report for
the observed run.)
"""

from __future__ import annotations

import logging

import pytest

from kairix.worker import (
    ConnectorSyncResult,
    dispatch_skills_sync,
)
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.integration

_ON_MARKER = "skills connector running (flag ON)"
_OFF_MARKER = "skills connector gated off (flag OFF)"


def test_skills_flag_off_branch_runs(caplog: pytest.LogCaptureFixture) -> None:
    """OFF branch — skills connector slot is a no-op; ON does not fire."""
    resolver = FakeFeatureFlagResolver().with_flag("connector_skills", False)

    on_calls = {"n": 0}

    def _never_on() -> ConnectorSyncResult:
        on_calls["n"] += 1
        return ConnectorSyncResult(synced=99, failed=0, dead_letter_added=0)

    with caplog.at_level(logging.INFO, logger="kairix.worker"):
        result = dispatch_skills_sync(
            read_flag=resolver.get,
            on_branch=_never_on,
        )

    messages = [rec.getMessage() for rec in caplog.records]
    assert any(_OFF_MARKER in m for m in messages), f"flag OFF must route through the OFF branch; logs={messages!r}"
    assert not any(_ON_MARKER in m for m in messages), (
        f"flag OFF must NOT route through the ON branch; logs={messages!r}"
    )
    assert on_calls["n"] == 0, "ON branch must not run when flag is OFF"
    assert result.synced == 0, f"OFF branch must return zero counters; got {result}"


def test_skills_flag_on_branch_runs(caplog: pytest.LogCaptureFixture) -> None:
    """ON branch — skills connector ON branch helper fires; OFF does not.

    The ON branch is wrapped with a marker-emitting stub here so the
    integration test doesn't drive a real connector-pipeline run (that's
    the E2E test's job). The branch selection — gated by the flag — is
    the property under test.
    """
    resolver = FakeFeatureFlagResolver().with_flag("connector_skills", True)

    off_calls = {"n": 0}

    def _never_off() -> ConnectorSyncResult:
        off_calls["n"] += 1
        return ConnectorSyncResult(synced=0, failed=0, dead_letter_added=0)

    def _wrapped_on() -> ConnectorSyncResult:
        logging.getLogger("kairix.worker").info(_ON_MARKER)
        return ConnectorSyncResult(synced=1, failed=0, dead_letter_added=0)

    with caplog.at_level(logging.INFO, logger="kairix.worker"):
        result = dispatch_skills_sync(
            read_flag=resolver.get,
            on_branch=_wrapped_on,
            off_branch=_never_off,
        )

    messages = [rec.getMessage() for rec in caplog.records]
    assert any(_ON_MARKER in m for m in messages), f"flag ON must route through the ON branch; logs={messages!r}"
    assert not any(_OFF_MARKER in m for m in messages), (
        f"flag ON must NOT route through the OFF branch; logs={messages!r}"
    )
    assert off_calls["n"] == 0, "OFF branch must not run when flag is ON"
    assert result.synced == 1, f"ON branch must have run and returned its result; got {result}"
