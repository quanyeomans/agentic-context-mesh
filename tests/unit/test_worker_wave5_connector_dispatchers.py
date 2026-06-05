"""Unit coverage for Wave-5 connector dispatchers (gmail, google_drive,
apple_caldav, google_calendar).

Post-cutover (task #132) the ``topology_v2_*`` flags retired and the
dispatch shape collapsed from `if read_flag(...): on_branch() else:
off_branch()` to `return on_branch()`. ``connector_gmail`` (introduce
stage) still gates the gmail dispatch via the OFF/ON branch shape.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from kairix.worker import (
    ConnectorSyncResult,
    dispatch_apple_caldav_sync,
    dispatch_gmail_sync,
    dispatch_google_calendar_sync,
    dispatch_google_drive_sync,
    gmail_off_branch_noop,
)

pytestmark = pytest.mark.unit


def _on_branch_sentinel() -> ConnectorSyncResult:
    """ON-branch substitute — records the call by returning a distinct value
    the test can assert on. F6-clean: a real callable, not None."""
    return ConnectorSyncResult(synced=42, failed=0, dead_letter_added=0)


def _off_branch_sentinel() -> ConnectorSyncResult:
    """OFF-branch substitute — distinct shape so the test can assert which
    branch fired."""
    return ConnectorSyncResult(synced=0, failed=99, dead_letter_added=0)


def test_gmail_dispatcher_on_branch_fires_when_connector_flag_true() -> None:
    """``connector_gmail`` OFF → noop; ON → on_branch result returned.

    Sabotage: invert the `if read_flag(...)` predicate in
    dispatch_gmail_sync → this test fails because the OFF sentinel
    comes back instead of the ON sentinel.
    """
    result = dispatch_gmail_sync(
        read_flag=lambda name: name == "connector_gmail",
        on_branch=_on_branch_sentinel,
        off_branch=_off_branch_sentinel,
    )

    assert result.synced == 42, "dispatch_gmail_sync did not route to ON branch"
    assert result.failed == 0


def test_gmail_dispatcher_off_branch_fires_when_flag_false() -> None:
    """When the flag reader returns False, the OFF branch runs (no-op).

    Sabotage: remove the `else` arm in dispatch_gmail_sync → this test
    fails because the return value is None / wrong shape.
    """
    result = dispatch_gmail_sync(
        read_flag=lambda _name: False,
        on_branch=_on_branch_sentinel,
        off_branch=_off_branch_sentinel,
    )

    assert result.failed == 99, "dispatch_gmail_sync did not route to OFF branch"
    assert result.synced == 0


def test_gmail_off_branch_noop_returns_zero_counters_and_logs_gate_signal(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``gmail_off_branch_noop`` returns the zero-counter result and
    emits an INFO log so operators can grep "connector gated off" to
    see which connectors were skipped during a tick.

    Sabotage: drop the ``logger.info(...)`` call from the noop → this
    test fails because the expected log record is absent.
    """
    with caplog.at_level(logging.INFO, logger="kairix.worker"):
        result = gmail_off_branch_noop()

    assert isinstance(result, ConnectorSyncResult)
    assert result.synced == 0
    assert result.failed == 0
    assert result.dead_letter_added == 0

    gated_off_records = [r for r in caplog.records if "gated off (flag OFF)" in r.getMessage()]
    assert len(gated_off_records) == 1, f"expected one 'gated off' INFO log; got {len(gated_off_records)}"


@pytest.mark.parametrize(
    "dispatcher",
    [
        dispatch_google_drive_sync,
        dispatch_apple_caldav_sync,
        dispatch_google_calendar_sync,
    ],
    ids=["google_drive", "apple_caldav", "google_calendar"],
)
def test_post_cutover_dispatcher_always_runs_on_branch(dispatcher: Any) -> None:
    """``topology_v2_*`` retired post-cutover (task #132); the OFF/skip
    branch is gone. The dispatcher always runs the on_branch helper.

    Sabotage: re-introduce a flag gate that skips on_branch → this test
    fails because the sentinel value comes back wrong.
    """
    result = dispatcher(on_branch=_on_branch_sentinel)

    assert result.synced == 42, f"{dispatcher.__name__} did not route to ON branch"
    assert result.failed == 0
