"""Unit coverage for Wave-5 connector dispatchers added across W2A-W2D
(gmail, google_drive, apple_caldav, google_calendar). Each dispatcher
is a 3-line `if read_flag(...): on_branch() else: off_branch()` shim;
the F7 + F9 per-file coverage floors on ``kairix/worker.py`` slipped
after the four functions landed without matching unit coverage (the
agents added integration coverage but not unit). This module restores
the floors by exercising the OFF / ON branches of each dispatcher with
a stubbed flag reader, plus the helper off-branch noops + run_via
shims they each ship.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from kairix.worker import (
    ConnectorSyncResult,
    apple_caldav_off_branch_noop,
    dispatch_apple_caldav_sync,
    dispatch_gmail_sync,
    dispatch_google_calendar_sync,
    dispatch_google_drive_sync,
    gmail_off_branch_noop,
    google_calendar_off_branch_noop,
    google_drive_off_branch_noop,
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


@pytest.mark.parametrize(
    ("dispatcher", "flag_name"),
    [
        (dispatch_gmail_sync, "connector_gmail"),
        (dispatch_google_drive_sync, "topology_v2_google_drive"),
        (dispatch_apple_caldav_sync, "topology_v2_apple_caldav"),
        (dispatch_google_calendar_sync, "topology_v2_google_calendar"),
    ],
    ids=["gmail", "google_drive", "apple_caldav", "google_calendar"],
)
def test_wave5_dispatcher_on_branch_fires_when_flag_true(dispatcher: Any, flag_name: str) -> None:
    """When the flag reader returns True for the dispatcher's flag, the
    ON branch runs and its result is returned. Sabotage: invert the
    `if read_flag(...)` predicate — this test fails because the OFF
    sentinel comes back instead of the ON sentinel.
    """
    result = dispatcher(
        read_flag=lambda name: name == flag_name,
        on_branch=_on_branch_sentinel,
        off_branch=_off_branch_sentinel,
    )

    assert result.synced == 42, f"{dispatcher.__name__} did not route to ON branch"
    assert result.failed == 0


@pytest.mark.parametrize(
    "dispatcher",
    [
        dispatch_gmail_sync,
        dispatch_google_drive_sync,
        dispatch_apple_caldav_sync,
        dispatch_google_calendar_sync,
    ],
    ids=["gmail", "google_drive", "apple_caldav", "google_calendar"],
)
def test_wave5_dispatcher_off_branch_fires_when_flag_false(dispatcher: Any) -> None:
    """When the flag reader returns False, the OFF branch runs (no-op).
    Sabotage: remove the `else` arm — this test fails because the
    return value is None / wrong shape.
    """
    result = dispatcher(
        read_flag=lambda _name: False,
        on_branch=_on_branch_sentinel,
        off_branch=_off_branch_sentinel,
    )

    assert result.failed == 99, f"{dispatcher.__name__} did not route to OFF branch"
    assert result.synced == 0


@pytest.mark.parametrize(
    "noop",
    [
        gmail_off_branch_noop,
        google_drive_off_branch_noop,
        apple_caldav_off_branch_noop,
        google_calendar_off_branch_noop,
    ],
    ids=["gmail", "google_drive", "apple_caldav", "google_calendar"],
)
def test_wave5_off_branch_noops_return_zero_counters_and_log_gate_signal(
    noop: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """Each off_branch_noop returns the zero-counter ConnectorSyncResult
    and emits an INFO log so operators can grep "connector gated off"
    to see which connectors were skipped during a tick.

    Sabotage: drop the ``logger.info(...)`` call from the noop — this
    test fails because the expected log record is absent.
    """
    with caplog.at_level(logging.INFO, logger="kairix.worker"):
        result = noop()

    assert isinstance(result, ConnectorSyncResult)
    assert result.synced == 0
    assert result.failed == 0
    assert result.dead_letter_added == 0

    gated_off_records = [r for r in caplog.records if "gated off (flag OFF)" in r.getMessage()]
    assert len(gated_off_records) == 1, f"expected one 'gated off' INFO log; got {len(gated_off_records)}"
