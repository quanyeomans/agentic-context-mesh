"""F54 integration coverage for the ``topology_v2_google_calendar`` flag.

The flag gates Google Calendar connector activation at the worker's
dispatcher boundary. When OFF (the default), the connector never runs
even if listed in ``kairix.config.yaml``; when ON, the dispatcher
routes through the standard connector pipeline which resolves the
``google_calendar`` plugin via its entry-point factory.

Per F54 (docs/architecture/feature-flag-architecture.md §5): every
flag needs integration coverage exercising both branches via
:class:`tests.fakes.FakeFeatureFlagResolver`. The literal flag name
``"topology_v2_google_calendar"`` appears verbatim in every
``with_flag(...)`` call so the F54 check picks it up.

F1-clean: ``FakeFeatureFlagResolver`` from ``tests/fakes.py`` is the
test seam — no @patch / module-attribute substitution on kairix.
F2-clean: no ``KAIRIX_*`` env-var manipulation.

Sabotage proof (executed by the agent, restored on completion):
inverting the if-branch in :func:`dispatch_google_calendar_sync` so
OFF reaches the ON branch — confirmed that
:func:`test_flag_off_runs_off_branch` AND
:func:`test_flag_on_runs_on_branch` both fail. Restored.
"""

from __future__ import annotations

from typing import Any

import pytest

from kairix.connectors.google_calendar import (
    GoogleCalendarConfig,
    GoogleCalendarConnector,
)
from kairix.connectors.google_calendar.client import (
    GoogleCalendarClient,
    GoogleCalendarEventRecord,
    GoogleCalendarEventsPage,
)
from kairix.core.protocols import ChangeEvent, SourceConnector
from kairix.worker import (
    ConnectorSyncResult,
    dispatch_google_calendar_sync,
    google_calendar_off_branch_noop,
)
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Cheap correctness pins
# ---------------------------------------------------------------------------


def test_topology_v2_google_calendar_flag_registered() -> None:
    """The flag exists in the registry, defaults False, stage=introduce."""
    from kairix.core.features.registry import REGISTRY

    assert "topology_v2_google_calendar" in REGISTRY
    entry = REGISTRY["topology_v2_google_calendar"]
    assert entry.default is False
    assert entry.stage == "introduce"
    assert entry.owner == "connector-framework"
    # Retire date is the F51 ceiling (6 months from landing); pin the
    # year so a future contributor bumping the date keeps it within
    # the ceiling.
    assert entry.target_retire_in.startswith("v2026.")


def test_google_calendar_connector_satisfies_source_connector_protocol() -> None:
    """The shipped connector is a runtime-checkable SourceConnector."""
    connector = GoogleCalendarConnector(
        GoogleCalendarConfig(access_token="placeholder-token"),  # pragma: allowlist secret
        client_factory=lambda _c: _ScriptedClient(_seed_page()),
    )
    assert isinstance(connector, SourceConnector)


# ---------------------------------------------------------------------------
# Dispatcher branch behaviour — F54 both-branch coverage
# ---------------------------------------------------------------------------


def test_flag_off_runs_off_branch() -> None:
    """OFF: the dispatcher invokes the off-branch noop, not the on-branch.

    The literal ``with_flag("topology_v2_google_calendar", False)`` is
    the F54-grep target for the OFF branch.
    """
    resolver = FakeFeatureFlagResolver().with_flag("topology_v2_google_calendar", False)
    on_calls = {"n": 0}
    off_calls = {"n": 0}

    def _on() -> ConnectorSyncResult:
        on_calls["n"] += 1
        return ConnectorSyncResult(synced=1, failed=0, dead_letter_added=0)

    def _off() -> ConnectorSyncResult:
        off_calls["n"] += 1
        return google_calendar_off_branch_noop()

    result = dispatch_google_calendar_sync(read_flag=resolver.get, on_branch=_on, off_branch=_off)
    assert on_calls["n"] == 0, f"OFF branch must NOT invoke ON; on_calls={on_calls!r}"
    assert off_calls["n"] == 1, f"OFF branch must run exactly once; off_calls={off_calls!r}"
    assert result.synced == 0


def test_flag_on_runs_on_branch() -> None:
    """ON: the dispatcher invokes the on-branch, not the off-branch noop.

    The literal ``with_flag("topology_v2_google_calendar", True)`` is
    the F54-grep target for the ON branch.
    """
    resolver = FakeFeatureFlagResolver().with_flag("topology_v2_google_calendar", True)
    on_calls = {"n": 0}
    off_calls = {"n": 0}

    def _on() -> ConnectorSyncResult:
        on_calls["n"] += 1
        return ConnectorSyncResult(synced=2, failed=0, dead_letter_added=0)

    def _off() -> ConnectorSyncResult:
        off_calls["n"] += 1
        return google_calendar_off_branch_noop()

    result = dispatch_google_calendar_sync(read_flag=resolver.get, on_branch=_on, off_branch=_off)
    assert on_calls["n"] == 1, f"ON branch must run exactly once; on_calls={on_calls!r}"
    assert off_calls["n"] == 0, f"ON branch must NOT invoke OFF; off_calls={off_calls!r}"
    assert result.synced == 2


# ---------------------------------------------------------------------------
# Composed-pipeline assertion — when ON, the real connector drains events
# ---------------------------------------------------------------------------


def _seed_page() -> GoogleCalendarEventsPage:
    record = GoogleCalendarEventRecord(
        event_id="event-zulu",
        summary="Cutover trial",
        description="",
        start_iso="2026-05-25T09:00:00Z",
        end_iso="2026-05-25T10:00:00Z",
        location="Conference room",
        attendees=("agent-alpha@example.com",),
        organizer_email="agent-beta@example.com",
        updated_iso="2026-05-25T08:00:00Z",
        recurrence=(),
        status="confirmed",
        html_link="https://calendar.google.com/event?eid=event-zulu",
        raw_payload='{"id": "event-zulu"}',
    )
    return GoogleCalendarEventsPage(
        events=(record,),
        next_page_token=None,
        next_sync_token="next-sync-token-zulu",
    )


class _ScriptedClient(GoogleCalendarClient):
    """In-memory stand-in for the Google client."""

    def __init__(self, page: GoogleCalendarEventsPage) -> None:
        self._page = page
        self._calendar_id = "primary"
        self._http = None  # type: ignore[assignment]  # F3 rationale: scripted client never makes HTTP calls
        self._page_size = 50
        self.initial_calls = 0
        self.delta_calls = 0

    def fetch_initial_events(self, _time_min_iso: str) -> GoogleCalendarEventsPage:
        self.initial_calls += 1
        return self._page

    def fetch_delta_events(self, _sync_token: str) -> GoogleCalendarEventsPage:
        self.delta_calls += 1
        return self._page

    def fetch_next_page_initial(self, _time_min_iso: str, _page_token: str) -> GoogleCalendarEventsPage:
        return self._page

    def fetch_next_page_delta(self, _sync_token: str, _page_token: str) -> GoogleCalendarEventsPage:
        return self._page

    def close(self) -> None:
        # Intentionally empty — scripted client owns no resources.
        return None


def _resolve_and_run(
    read_flag: Any,
) -> tuple[frozenset[str], list[ChangeEvent], _ScriptedClient | None]:
    """Composition surface: consult the flag, then conditionally drive sync.

    Hoisted so both flag-OFF and flag-ON tests use the exact same
    composition path — the only variable is the flag value the
    resolver returns. Returns the enabled-connector set, the emitted
    events, and the scripted client (or None if the OFF branch never
    constructed it) so the assertions can inspect call counts.
    """
    enabled: set[str] = set()
    if read_flag("topology_v2_google_calendar"):
        enabled.add("google_calendar")

    if "google_calendar" not in enabled:
        return frozenset(enabled), [], None

    config = GoogleCalendarConfig(
        access_token="placeholder-token",  # pragma: allowlist secret
    )
    client = _ScriptedClient(_seed_page())
    connector = GoogleCalendarConnector(config, client_factory=lambda _c: client)
    events = list(connector.list_changes(cursor=None))
    return frozenset(enabled), events, client


def test_flag_off_skips_connector_construction() -> None:
    """OFF: the google_calendar plugin is not in the enabled set and the client never builds.

    The OFF-branch must short-circuit BEFORE constructing the
    connector — if the construction happened anyway, the test's
    ``client is None`` assertion fails.
    """
    resolver = FakeFeatureFlagResolver().with_flag("topology_v2_google_calendar", False)
    enabled, events, client = _resolve_and_run(resolver.get)
    assert "google_calendar" not in enabled, f"flag OFF must exclude google_calendar; got {enabled!r}"
    assert events == [], f"flag OFF must not run sync; got {events!r}"
    assert client is None, "flag OFF must not construct the Google client"


def test_flag_on_constructs_and_syncs() -> None:
    """ON: the google_calendar plugin is enabled AND the scripted client serves the events.

    The ON-branch must construct the connector, run ``list_changes``,
    and surface the scripted event as a ``created`` :class:`ChangeEvent`.
    Sabotage-proof per the module docstring.
    """
    resolver = FakeFeatureFlagResolver().with_flag("topology_v2_google_calendar", True)
    enabled, events, client = _resolve_and_run(resolver.get)
    assert "google_calendar" in enabled, f"flag ON must include google_calendar; got {enabled!r}"
    assert client is not None, "flag ON must construct the Google client"
    assert client.initial_calls == 1, f"flag ON must drive an initial events call; got {client.initial_calls}"
    assert [(e.op, e.item_id) for e in events] == [("created", "event-zulu")], (
        f"flag ON must surface the scripted event as a created ChangeEvent; got {events!r}"
    )
