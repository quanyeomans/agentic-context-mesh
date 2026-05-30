"""Step implementations for connector_apple_caldav.feature.

The scenarios drive the real
:class:`kairix.connectors.apple_caldav.AppleCalDavConnector` against
a stand-in :class:`AppleCalDavClient` so no real iCloud traffic
fires. Per F46, this binding stays within depth-2 of either the
connector's factory or the canonical fake — direct
``AppleCalDavConnector(...)`` construction is allowed because the
connector is itself a Protocol-compliant leaf (no pipeline composed
here).

F1-clean: no @patch / module-attribute substitution on kairix. The DI
seam used here (``client_factory``) is a real callable kwarg the
production constructor takes, not a monkey-patch.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.connectors.apple_caldav import (
    AppleCalDavClient,
    AppleCalDavConfig,
    AppleCalDavConnector,
    CalDavCalendarRef,
    CalendarEventRecord,
    CalendarSyncPage,
)
from kairix.core.protocols import ChangeEvent

_FIXTURE_CALENDAR_URL = "https://caldav.icloud.com/12345/calendars/personal/"


@dataclass
class _Ctx:
    """Per-scenario context — no module-level mutable state."""

    config: AppleCalDavConfig | None = None
    connector: AppleCalDavConnector | None = None
    scripted_pages: list[CalendarSyncPage] | None = None
    persisted_cursor: str | None = None
    last_events: list[ChangeEvent] | None = None
    previously_seen_event_id: str | None = None


@pytest.fixture
def apple_caldav_ctx() -> _Ctx:
    return _Ctx()


class _ScriptedClient(AppleCalDavClient):
    """In-memory stand-in for :class:`AppleCalDavClient`.

    Drains a queue of pre-built :class:`CalendarSyncPage` instances on
    successive :meth:`list_changes` calls. Never opens the
    :mod:`caldav` DAVClient or runs HTTP — the BDD layer doesn't model
    the transport surface; that's the unit-test layer's concern.
    """

    def __init__(self, pages: list[CalendarSyncPage]) -> None:
        # Skip the real __init__ — no auth, no caldav library import.
        self._username = "agent-alpha@example.com"
        self._password = "fixture-app-password"  # pragma: allowlist secret — test fixture
        self._endpoint = "https://caldav.icloud.com"
        self._dav_client_factory = None
        self._dav_client = None
        self._queue = list(pages)

    def discover_calendars(self) -> tuple[CalDavCalendarRef, ...]:
        return (
            CalDavCalendarRef(
                url=_FIXTURE_CALENDAR_URL,
                display_name="Personal",
                ctag="ctag-fresh-1",
            ),
        )

    def list_changes(self, calendar_url: str, sync_token: str | None) -> CalendarSyncPage:
        del calendar_url, sync_token
        return self._queue.pop(0)

    def fetch(self, event_url: str) -> CalendarEventRecord:
        del event_url
        raise NotImplementedError


def _build_connector(ctx: _Ctx) -> AppleCalDavConnector:
    """Construct the real connector backed by a scripted client factory."""
    assert ctx.scripted_pages is not None, "Given step must seed scripted_pages first"

    config = ctx.config or AppleCalDavConfig(
        username="agent-alpha@example.com",
        password="fixture-app-password",  # pragma: allowlist secret — test fixture
    )
    ctx.config = config

    pages = list(ctx.scripted_pages)
    return AppleCalDavConnector(
        config,
        client_factory=lambda _c: _ScriptedClient(pages),
    )


def _event_record(
    event_id: str,
    summary: str = "Team sync",
    attendee: str = "agent-alpha@example.com",
) -> CalendarEventRecord:
    return CalendarEventRecord(
        event_id=event_id,
        summary=summary,
        dtstart_iso="2026-05-25T09:00:00Z",
        dtend_iso="2026-05-25T10:00:00Z",
        location="Conference room",
        attendees=(attendee,),
        organiser="agent-organiser@example.com",
        last_modified_iso="2026-05-25T08:00:00Z",
        recurrence_rule="",
        cancelled=False,
        removed=False,
        raw_ics=("BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:" + event_id + "\nEND:VEVENT\nEND:VCALENDAR\n"),
        event_url=_FIXTURE_CALENDAR_URL + event_id + ".ics",
    )


def _cancelled_record(event_id: str) -> CalendarEventRecord:
    base = _event_record(event_id)
    return CalendarEventRecord(
        event_id=base.event_id,
        summary=base.summary,
        dtstart_iso=base.dtstart_iso,
        dtend_iso=base.dtend_iso,
        location=base.location,
        attendees=base.attendees,
        organiser=base.organiser,
        last_modified_iso="2026-05-25T11:00:00Z",
        recurrence_rule="",
        cancelled=True,
        removed=False,
        raw_ics=base.raw_ics,
        event_url=base.event_url,
    )


# ---------------------------------------------------------------------------
# Givens
# ---------------------------------------------------------------------------


@given(parsers.parse("an operator-configured iCloud account with two scheduled events"))
def _icloud_with_two_events(apple_caldav_ctx: _Ctx) -> None:
    apple_caldav_ctx.scripted_pages = [
        CalendarSyncPage(
            events=(_event_record("event-alpha"), _event_record("event-bravo")),
            sync_token="caldav-sync-token-initial",
        )
    ]


@given(parsers.parse("an operator-configured iCloud account with one previously synced event"))
def _icloud_with_one_previously_synced(apple_caldav_ctx: _Ctx) -> None:
    apple_caldav_ctx.previously_seen_event_id = "event-alpha"
    apple_caldav_ctx.persisted_cursor = f"{_FIXTURE_CALENDAR_URL}=caldav-sync-token-prior"


@given(parsers.parse("the CalDAV sync REPORT returns one new event plus the previously seen id"))
def _sync_returns_new_plus_seen(apple_caldav_ctx: _Ctx) -> None:
    apple_caldav_ctx.scripted_pages = [
        CalendarSyncPage(
            events=(
                _event_record(apple_caldav_ctx.previously_seen_event_id or "event-alpha"),
                _event_record("event-charlie"),
            ),
            sync_token="caldav-sync-token-after",
        )
    ]


@given(parsers.parse("the CalDAV sync REPORT returns that event marked as cancelled"))
def _sync_returns_cancelled(apple_caldav_ctx: _Ctx) -> None:
    apple_caldav_ctx.scripted_pages = [
        CalendarSyncPage(
            events=(_cancelled_record(apple_caldav_ctx.previously_seen_event_id or "event-alpha"),),
            sync_token="caldav-sync-token-cancelled",
        )
    ]


# ---------------------------------------------------------------------------
# Whens
# ---------------------------------------------------------------------------


@when(parsers.parse("the operator runs the apple_caldav connector list_changes with no cursor"))
def _run_list_changes_no_cursor(apple_caldav_ctx: _Ctx) -> None:
    connector = _build_connector(apple_caldav_ctx)
    apple_caldav_ctx.connector = connector
    apple_caldav_ctx.last_events = list(connector.list_changes(cursor=None))


@when(parsers.parse("the operator runs the apple_caldav connector list_changes with the persisted sync token"))
def _run_list_changes_with_cursor(apple_caldav_ctx: _Ctx) -> None:
    connector = _build_connector(apple_caldav_ctx)
    # Pretend the orchestrator already saw the previously synced event
    # in an earlier process — seeding the connector's known-id set so a
    # sync page repeat surfaces as modified, not as created.
    if apple_caldav_ctx.previously_seen_event_id is not None:
        connector.seed_known_ids([apple_caldav_ctx.previously_seen_event_id])
    apple_caldav_ctx.connector = connector
    apple_caldav_ctx.last_events = list(connector.list_changes(cursor=apple_caldav_ctx.persisted_cursor))


# ---------------------------------------------------------------------------
# Thens
# ---------------------------------------------------------------------------


@then(parsers.parse("two created change events are emitted in event-id order from apple_caldav"))
def _two_created_events(apple_caldav_ctx: _Ctx) -> None:
    events = apple_caldav_ctx.last_events or []
    created = [e for e in events if e.op == "created"]
    assert len(created) == 2, f"expected exactly two created events, got {created!r}"
    assert [e.item_id for e in created] == ["event-alpha", "event-bravo"]


@then(parsers.parse("every apple_caldav change event carries a non-empty event id as item_id"))
def _every_event_has_item_id(apple_caldav_ctx: _Ctx) -> None:
    events = apple_caldav_ctx.last_events or []
    for ev in events:
        assert ev.item_id, f"event missing item_id: {ev!r}"


@then(parsers.parse("every apple_caldav change event metadata payload exposes summary and attendees"))
def _every_event_has_metadata(apple_caldav_ctx: _Ctx) -> None:
    events = apple_caldav_ctx.last_events or []
    for ev in events:
        metadata: Any = ev.metadata
        assert "summary" in metadata, f"event {ev.item_id!r} missing summary metadata: {metadata!r}"
        assert "attendees" in metadata, f"event {ev.item_id!r} missing attendees metadata: {metadata!r}"


@then(parsers.parse("exactly one created change event is emitted for the new event id from apple_caldav"))
def _one_new_created(apple_caldav_ctx: _Ctx) -> None:
    events = apple_caldav_ctx.last_events or []
    created = [e for e in events if e.op == "created"]
    assert [e.item_id for e in created] == ["event-charlie"], f"unexpected created events: {created!r}"


@then(parsers.parse("the previously seen event id surfaces as a modified change event from apple_caldav"))
def _previously_seen_modified(apple_caldav_ctx: _Ctx) -> None:
    events = apple_caldav_ctx.last_events or []
    modified = [e for e in events if e.op == "modified"]
    target = apple_caldav_ctx.previously_seen_event_id
    assert any(e.item_id == target for e in modified), (
        f"expected modified event for previously seen id {target!r}, got {modified!r}"
    )


@then(parsers.parse("the apple_caldav connector exposes a persisted sync token as the next cursor"))
def _sync_token_exposed(apple_caldav_ctx: _Ctx) -> None:
    connector = apple_caldav_ctx.connector
    assert connector is not None, "When step must run before Then"
    cursor = connector.next_cursor()
    assert cursor, f"expected sync-token cursor to be exposed; got {cursor!r}"


@then(parsers.parse("a deleted change event is emitted for the cancelled event id from apple_caldav"))
def _deleted_for_cancelled(apple_caldav_ctx: _Ctx) -> None:
    events = apple_caldav_ctx.last_events or []
    deleted = [e for e in events if e.op == "deleted"]
    target = apple_caldav_ctx.previously_seen_event_id
    assert [e.item_id for e in deleted] == [target], (
        f"expected exactly one deleted event for {target!r}, got {deleted!r}"
    )


@then(parsers.parse("the apple_caldav connector still exposes a persisted sync token for the next cursor"))
def _sync_token_still_exposed(apple_caldav_ctx: _Ctx) -> None:
    connector = apple_caldav_ctx.connector
    assert connector is not None, "When step must run before Then"
    cursor = connector.next_cursor()
    assert cursor, f"expected sync-token cursor to remain exposed; got {cursor!r}"
