"""Step implementations for connector_google_calendar.feature.

The scenarios drive the real
:class:`kairix.connectors.google_calendar.GoogleCalendarConnector`
against a scripted :class:`GoogleCalendarClient` so no real network
traffic fires. Per F46, this binding stays within depth-2 of either
the connector's factory (``make_connector``) or the canonical fake —
direct ``GoogleCalendarConnector(...)`` construction is allowed
because the connector is itself a Protocol-compliant leaf (no
pipeline composed here).

F1-clean: no @patch / module-attribute substitution on kairix. The
DI seam used here (``client_factory``) is a real callable kwarg the
production constructor takes, not a monkey-patch.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.connectors.google_calendar import (
    GoogleCalendarConfig,
    GoogleCalendarConnector,
)
from kairix.connectors.google_calendar.client import (
    GoogleCalendarClient,
    GoogleCalendarEventRecord,
    GoogleCalendarEventsPage,
)
from kairix.core.protocols import ChangeEvent


@dataclass
class _Ctx:
    """Per-scenario context — no module-level mutable state."""

    config: GoogleCalendarConfig | None = None
    connector: GoogleCalendarConnector | None = None
    scripted_pages: list[GoogleCalendarEventsPage] | None = None
    persisted_cursor: str | None = None
    last_events: list[ChangeEvent] | None = None
    previously_seen_event_id: str | None = None
    recurring_master_id: str | None = None


@pytest.fixture
def google_calendar_ctx() -> _Ctx:
    return _Ctx()


class _ScriptedClient(GoogleCalendarClient):
    """In-memory stand-in for :class:`GoogleCalendarClient`.

    Drains a queue of pre-built :class:`GoogleCalendarEventsPage`
    instances on successive calls. Never opens an httpx client — the
    BDD layer doesn't model the transport surface; that's the unit-
    test layer's concern.
    """

    def __init__(self, pages: list[GoogleCalendarEventsPage]) -> None:
        # Skip the real __init__ — no http_client needed.
        self._queue = list(pages)
        self._calendar_id = "primary"
        self._http = None  # type: ignore[assignment]  # F3 rationale: scripted client never makes HTTP calls
        self._page_size = 50

    def fetch_initial_events(self, _time_min_iso: str) -> GoogleCalendarEventsPage:
        return self._queue.pop(0)

    def fetch_delta_events(self, _sync_token: str) -> GoogleCalendarEventsPage:
        return self._queue.pop(0)

    def fetch_next_page_initial(self, _time_min_iso: str, _page_token: str) -> GoogleCalendarEventsPage:
        return self._queue.pop(0)

    def fetch_next_page_delta(self, _sync_token: str, _page_token: str) -> GoogleCalendarEventsPage:
        return self._queue.pop(0)

    def close(self) -> None:
        # Intentionally empty — scripted client owns no resources.
        return None


def _build_connector(ctx: _Ctx) -> GoogleCalendarConnector:
    """Construct the real connector backed by a scripted client factory."""
    assert ctx.scripted_pages is not None, "Given step must seed scripted_pages first"

    config = ctx.config or GoogleCalendarConfig(
        # Placeholder token — the scripted client never invokes the
        # auth flow because :class:`_ScriptedClient.fetch_*` short-
        # circuits to the in-memory page queue.
        access_token="placeholder-token",  # pragma: allowlist secret — test fixture
    )
    ctx.config = config

    pages = list(ctx.scripted_pages)
    return GoogleCalendarConnector(
        config,
        client_factory=lambda _c: _ScriptedClient(pages),
    )


def _event_record(
    event_id: str,
    summary: str = "Team sync",
    attendee: str = "agent-alpha@example.com",
    recurrence: tuple[str, ...] = (),
    status: str = "confirmed",
) -> GoogleCalendarEventRecord:
    return GoogleCalendarEventRecord(
        event_id=event_id,
        summary=summary,
        description="",
        start_iso="2026-05-25T09:00:00Z",
        end_iso="2026-05-25T10:00:00Z",
        location="Conference room",
        attendees=(attendee,),
        organizer_email="agent-beta@example.com",
        updated_iso="2026-05-25T08:00:00Z",
        recurrence=recurrence,
        status=status,
        html_link=f"https://calendar.google.com/event?eid={event_id}",
        raw_payload='{"id": "' + event_id + '"}',
    )


# ---------------------------------------------------------------------------
# Givens
# ---------------------------------------------------------------------------


@given(parsers.parse("an operator-configured Google Calendar with two scheduled events"))
def _calendar_with_two_events(google_calendar_ctx: _Ctx) -> None:
    google_calendar_ctx.scripted_pages = [
        GoogleCalendarEventsPage(
            events=(_event_record("event-alpha"), _event_record("event-bravo")),
            next_page_token=None,
            next_sync_token="next-sync-token-initial",
        )
    ]


@given(parsers.parse("an operator-configured Google Calendar with one previously synced event"))
def _calendar_with_one_previously_synced(google_calendar_ctx: _Ctx) -> None:
    google_calendar_ctx.previously_seen_event_id = "event-alpha"
    google_calendar_ctx.persisted_cursor = "next-sync-token-prior"


@given(parsers.parse("the events list page returns one new event plus the previously seen id"))
def _events_page_new_plus_seen(google_calendar_ctx: _Ctx) -> None:
    google_calendar_ctx.scripted_pages = [
        GoogleCalendarEventsPage(
            events=(
                _event_record(google_calendar_ctx.previously_seen_event_id or "event-alpha"),
                _event_record("event-charlie"),
            ),
            next_page_token=None,
            next_sync_token="next-sync-token-after",
        )
    ]


@given(parsers.parse("the events list page returns that event marked as cancelled"))
def _events_page_cancelled(google_calendar_ctx: _Ctx) -> None:
    target = google_calendar_ctx.previously_seen_event_id or "event-alpha"
    google_calendar_ctx.scripted_pages = [
        GoogleCalendarEventsPage(
            events=(_event_record(target, status="cancelled"),),
            next_page_token=None,
            next_sync_token="next-sync-token-cancelled",
        )
    ]


@given(parsers.parse("an operator-configured Google Calendar with one recurring master event"))
def _calendar_with_recurring_master(google_calendar_ctx: _Ctx) -> None:
    google_calendar_ctx.recurring_master_id = "event-recurring-master"
    google_calendar_ctx.scripted_pages = [
        GoogleCalendarEventsPage(
            events=(
                _event_record(
                    google_calendar_ctx.recurring_master_id,
                    summary="Weekly standup",
                    recurrence=("RRULE:FREQ=WEEKLY;BYDAY=MO",),
                ),
            ),
            next_page_token=None,
            next_sync_token="next-sync-token-recurring",
        )
    ]


# ---------------------------------------------------------------------------
# Whens
# ---------------------------------------------------------------------------


@when(parsers.parse("the operator runs the google_calendar connector list_changes with no cursor"))
def _run_list_changes_no_cursor(google_calendar_ctx: _Ctx) -> None:
    connector = _build_connector(google_calendar_ctx)
    google_calendar_ctx.connector = connector
    google_calendar_ctx.last_events = list(connector.list_changes(cursor=None))


@when(parsers.parse("the operator runs the google_calendar connector list_changes with the persisted sync token"))
def _run_list_changes_with_cursor(google_calendar_ctx: _Ctx) -> None:
    connector = _build_connector(google_calendar_ctx)
    # Pretend the orchestrator already saw the previously synced event
    # in an earlier process — seeding the connector's known-id set so a
    # delta page repeat surfaces as modified, not as created.
    if google_calendar_ctx.previously_seen_event_id is not None:
        connector.seed_known_ids([google_calendar_ctx.previously_seen_event_id])
    google_calendar_ctx.connector = connector
    google_calendar_ctx.last_events = list(connector.list_changes(cursor=google_calendar_ctx.persisted_cursor))


# ---------------------------------------------------------------------------
# Thens
# ---------------------------------------------------------------------------


@then(parsers.parse("two google_calendar created change events are emitted in event-id order"))
def _two_created_events(google_calendar_ctx: _Ctx) -> None:
    events = google_calendar_ctx.last_events or []
    created = [e for e in events if e.op == "created"]
    assert len(created) == 2, f"expected exactly two created events, got {created!r}"
    assert [e.item_id for e in created] == ["event-alpha", "event-bravo"]


@then(parsers.parse("every google_calendar change event carries a non-empty Google event id as item_id"))
def _every_event_has_item_id(google_calendar_ctx: _Ctx) -> None:
    events = google_calendar_ctx.last_events or []
    for ev in events:
        assert ev.item_id, f"event missing item_id: {ev!r}"


@then(parsers.parse("every google_calendar change event metadata payload exposes summary and attendees"))
def _every_event_has_metadata(google_calendar_ctx: _Ctx) -> None:
    events = google_calendar_ctx.last_events or []
    for ev in events:
        metadata: Any = ev.metadata
        assert "summary" in metadata, f"event {ev.item_id!r} missing summary metadata: {metadata!r}"
        assert "attendees" in metadata, f"event {ev.item_id!r} missing attendees metadata: {metadata!r}"


@then(parsers.parse("exactly one google_calendar created change event is emitted for the new event id"))
def _one_new_created(google_calendar_ctx: _Ctx) -> None:
    events = google_calendar_ctx.last_events or []
    created = [e for e in events if e.op == "created"]
    assert [e.item_id for e in created] == ["event-charlie"], f"unexpected created events: {created!r}"


@then(parsers.parse("the previously seen google_calendar event id surfaces as a modified change event"))
def _previously_seen_modified(google_calendar_ctx: _Ctx) -> None:
    events = google_calendar_ctx.last_events or []
    modified = [e for e in events if e.op == "modified"]
    target = google_calendar_ctx.previously_seen_event_id
    assert any(e.item_id == target for e in modified), (
        f"expected modified event for previously seen id {target!r}, got {modified!r}"
    )


@then(parsers.parse("the google_calendar connector exposes a persisted sync token as the next cursor"))
def _sync_token_exposed(google_calendar_ctx: _Ctx) -> None:
    connector = google_calendar_ctx.connector
    assert connector is not None, "When step must run before Then"
    assert connector.next_cursor(), f"expected sync token cursor; got {connector.next_cursor()!r}"


@then(parsers.parse("no google_calendar change event is emitted for the cancelled event id"))
def _no_event_for_cancelled(google_calendar_ctx: _Ctx) -> None:
    events = google_calendar_ctx.last_events or []
    target = google_calendar_ctx.previously_seen_event_id
    matching = [e for e in events if e.item_id == target]
    assert matching == [], f"expected no events for cancelled id {target!r}, got {matching!r}"


@then(parsers.parse("the google_calendar connector still exposes a persisted sync token for the next cursor"))
def _sync_token_still_exposed(google_calendar_ctx: _Ctx) -> None:
    connector = google_calendar_ctx.connector
    assert connector is not None, "When step must run before Then"
    assert connector.next_cursor(), f"expected sync token cursor; got {connector.next_cursor()!r}"


@then(parsers.parse("exactly one google_calendar created change event is emitted for the recurring master id"))
def _one_created_for_master(google_calendar_ctx: _Ctx) -> None:
    events = google_calendar_ctx.last_events or []
    created = [e for e in events if e.op == "created"]
    target = google_calendar_ctx.recurring_master_id
    assert [e.item_id for e in created] == [target], (
        f"expected one created event for recurring master {target!r}; got {created!r}"
    )


@then(parsers.parse("the google_calendar connector source metadata for the recurring master id carries the RRULE"))
def _metadata_carries_rrule(google_calendar_ctx: _Ctx) -> None:
    connector = google_calendar_ctx.connector
    assert connector is not None, "When step must run before Then"
    target = google_calendar_ctx.recurring_master_id or ""
    md = connector.metadata_for(target)
    rrule = md.properties.get("recurrence_rule", "")
    assert "RRULE:FREQ=WEEKLY" in rrule, (
        f"expected RRULE captured in metadata.properties.recurrence_rule; got {md.properties!r}"
    )
