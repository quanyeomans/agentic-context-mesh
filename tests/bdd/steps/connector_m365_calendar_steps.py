"""Step implementations for connector_m365_calendar.feature.

The scenarios drive the real
:class:`kairix.connectors.m365_calendar.M365CalendarConnector` against
a stand-in :class:`M365GraphCalendarClient` constructed over an
``httpx.MockTransport`` so no real Graph traffic fires. Per F46, this
binding stays within depth-2 of either the connector's factory
(``make_connector``) or the canonical fake — direct
``M365CalendarConnector(...)`` construction is allowed because the
connector is itself a Protocol-compliant leaf (no pipeline composed
here).

F1-clean: no @patch / module-attribute substitution on kairix. The
DI seam used here (``client_factory``) is a real callable kwarg the
production constructor takes, not a monkey-patch.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.connectors.m365_calendar import M365CalendarConnector
from kairix.connectors.m365_calendar.connector import M365CalendarConfig
from kairix.connectors.m365_calendar.graph_client import (
    CalendarDeltaPage,
    CalendarEventRecord,
    M365GraphCalendarClient,
)
from kairix.core.protocols import ChangeEvent


@dataclass
class _Ctx:
    """Per-scenario context — no module-level mutable state."""

    config: M365CalendarConfig | None = None
    connector: M365CalendarConnector | None = None
    scripted_pages: list[CalendarDeltaPage] | None = None
    persisted_cursor: str | None = None
    last_events: list[ChangeEvent] | None = None
    previously_seen_event_id: str | None = None


@pytest.fixture
def m365_calendar_ctx() -> _Ctx:
    return _Ctx()


def _build_connector(ctx: _Ctx) -> M365CalendarConnector:
    """Construct the real connector backed by a scripted client factory."""
    assert ctx.scripted_pages is not None, "Given step must seed scripted_pages first"

    config = ctx.config or M365CalendarConfig(
        user_id="operator@example.com",
        # Placeholder ids — the scripted client never invokes the auth
        # flow because :class:`_ScriptedClient.fetch_*` short-circuits
        # to the in-memory page queue. Using literal placeholders keeps
        # F32 happy (no real names in fixtures) and F15 happy (no
        # plausible-secret-string leakage).
        tenant_id="placeholder-tenant",
        client_id="placeholder-client",
        client_secret="placeholder-secret",  # pragma: allowlist secret
    )
    ctx.config = config

    pages = list(ctx.scripted_pages)
    return M365CalendarConnector(
        config,
        client_factory=lambda _c: _ScriptedClient(pages),
    )


class _ScriptedClient(M365GraphCalendarClient):
    """In-memory stand-in for :class:`M365GraphCalendarClient`.

    Drains a queue of pre-built :class:`CalendarDeltaPage` instances on
    successive Graph calls. Never opens an httpx client or runs the
    OAuth2 flow — the BDD layer doesn't model the transport surface;
    that's the unit-test layer's concern.
    """

    def __init__(self, pages: list[CalendarDeltaPage]) -> None:
        # Skip the real __init__ — no auth, no http_client needed.
        self._queue = list(pages)
        self._user_id = "operator@example.com"
        self._http = None  # type: ignore[assignment]  # F3 rationale: scripted client never makes HTTP calls
        self._page_size = 50

    def fetch_initial_delta(self, _start_iso: str, _end_iso: str) -> CalendarDeltaPage:
        return self._queue.pop(0)

    def fetch_delta_page(self, _link: str) -> CalendarDeltaPage:
        return self._queue.pop(0)

    def close(self) -> None:
        # Intentionally empty — the scripted client owns no resources.
        return None


def _event_record(
    event_id: str,
    subject: str = "Team sync",
    attendee: str = "alpha@example.com",
) -> CalendarEventRecord:
    return CalendarEventRecord(
        event_id=event_id,
        subject=subject,
        start_iso="2026-05-25T09:00:00Z",
        end_iso="2026-05-25T10:00:00Z",
        location="Conference room",
        attendees=(attendee,),
        organiser="organiser@example.com",
        last_modified_iso="2026-05-25T08:00:00Z",
        cancelled=False,
        removed=False,
        raw_payload='{"id": "' + event_id + '"}',
    )


def _cancelled_record(event_id: str) -> CalendarEventRecord:
    base = _event_record(event_id)
    return CalendarEventRecord(
        event_id=base.event_id,
        subject=base.subject,
        start_iso=base.start_iso,
        end_iso=base.end_iso,
        location=base.location,
        attendees=base.attendees,
        organiser=base.organiser,
        last_modified_iso="2026-05-25T11:00:00Z",
        cancelled=True,
        removed=False,
        raw_payload=base.raw_payload,
    )


# ---------------------------------------------------------------------------
# Givens
# ---------------------------------------------------------------------------


@given(parsers.parse("an operator-configured M365 calendar with two scheduled events"))
def _calendar_with_two_events(m365_calendar_ctx: _Ctx) -> None:
    m365_calendar_ctx.scripted_pages = [
        CalendarDeltaPage(
            events=(_event_record("event-alpha"), _event_record("event-bravo")),
            next_link=None,
            delta_link="https://graph.microsoft.com/v1.0/.../$deltatoken=initial",
        )
    ]


@given(parsers.parse("an operator-configured M365 calendar with one previously synced event"))
def _calendar_with_one_previously_synced(m365_calendar_ctx: _Ctx) -> None:
    m365_calendar_ctx.previously_seen_event_id = "event-alpha"
    m365_calendar_ctx.persisted_cursor = "https://graph.microsoft.com/v1.0/.../$deltatoken=prior"


@given(parsers.parse("the Graph delta page returns one new event plus the previously seen id"))
def _delta_page_new_plus_seen(m365_calendar_ctx: _Ctx) -> None:
    m365_calendar_ctx.scripted_pages = [
        CalendarDeltaPage(
            events=(
                _event_record(m365_calendar_ctx.previously_seen_event_id or "event-alpha"),
                _event_record("event-charlie"),
            ),
            next_link=None,
            delta_link="https://graph.microsoft.com/v1.0/.../$deltatoken=after",
        )
    ]


@given(parsers.parse("the Graph delta page returns that event marked as cancelled"))
def _delta_page_cancelled(m365_calendar_ctx: _Ctx) -> None:
    m365_calendar_ctx.scripted_pages = [
        CalendarDeltaPage(
            events=(_cancelled_record(m365_calendar_ctx.previously_seen_event_id or "event-alpha"),),
            next_link=None,
            delta_link="https://graph.microsoft.com/v1.0/.../$deltatoken=cancelled",
        )
    ]


# ---------------------------------------------------------------------------
# Whens
# ---------------------------------------------------------------------------


@when(parsers.parse("the operator runs the m365_calendar connector list_changes with no cursor"))
def _run_list_changes_no_cursor(m365_calendar_ctx: _Ctx) -> None:
    connector = _build_connector(m365_calendar_ctx)
    m365_calendar_ctx.connector = connector
    m365_calendar_ctx.last_events = list(connector.list_changes(cursor=None))


@when(parsers.parse("the operator runs the m365_calendar connector list_changes with the persisted delta cursor"))
def _run_list_changes_with_cursor(m365_calendar_ctx: _Ctx) -> None:
    connector = _build_connector(m365_calendar_ctx)
    # Pretend the orchestrator already saw the previously synced event
    # in an earlier process — seeding the connector's known-id set so a
    # delta page repeat surfaces as modified, not as created.
    if m365_calendar_ctx.previously_seen_event_id is not None:
        connector.seed_known_ids([m365_calendar_ctx.previously_seen_event_id])
    m365_calendar_ctx.connector = connector
    m365_calendar_ctx.last_events = list(connector.list_changes(cursor=m365_calendar_ctx.persisted_cursor))


# ---------------------------------------------------------------------------
# Thens
# ---------------------------------------------------------------------------


@then(parsers.parse("two created change events are emitted in event-id order"))
def _two_created_events(m365_calendar_ctx: _Ctx) -> None:
    events = m365_calendar_ctx.last_events or []
    created = [e for e in events if e.op == "created"]
    assert len(created) == 2, f"expected exactly two created events, got {created!r}"
    assert [e.item_id for e in created] == ["event-alpha", "event-bravo"]


@then(parsers.parse("every change event carries a non-empty Graph event id as item_id"))
def _every_event_has_item_id(m365_calendar_ctx: _Ctx) -> None:
    events = m365_calendar_ctx.last_events or []
    for ev in events:
        assert ev.item_id, f"event missing item_id: {ev!r}"


@then(parsers.parse("every change event metadata payload exposes subject and attendees"))
def _every_event_has_metadata(m365_calendar_ctx: _Ctx) -> None:
    events = m365_calendar_ctx.last_events or []
    for ev in events:
        metadata: Any = ev.metadata
        assert "subject" in metadata, f"event {ev.item_id!r} missing subject metadata: {metadata!r}"
        assert "attendees" in metadata, f"event {ev.item_id!r} missing attendees metadata: {metadata!r}"


@then(parsers.parse("exactly one created change event is emitted for the new event id"))
def _one_new_created(m365_calendar_ctx: _Ctx) -> None:
    events = m365_calendar_ctx.last_events or []
    created = [e for e in events if e.op == "created"]
    assert [e.item_id for e in created] == ["event-charlie"], f"unexpected created events: {created!r}"


@then(parsers.parse("the previously seen event id surfaces as a modified change event"))
def _previously_seen_modified(m365_calendar_ctx: _Ctx) -> None:
    events = m365_calendar_ctx.last_events or []
    modified = [e for e in events if e.op == "modified"]
    target = m365_calendar_ctx.previously_seen_event_id
    assert any(e.item_id == target for e in modified), (
        f"expected modified event for previously seen id {target!r}, got {modified!r}"
    )


@then(parsers.parse("the connector exposes a persisted delta link as the next cursor"))
def _delta_link_exposed(m365_calendar_ctx: _Ctx) -> None:
    connector = m365_calendar_ctx.connector
    assert connector is not None, "When step must run before Then"
    assert connector.last_delta_link, f"expected delta link to be exposed; got {connector.last_delta_link!r}"


@then(parsers.parse("a deleted change event is emitted for the cancelled event id"))
def _deleted_for_cancelled(m365_calendar_ctx: _Ctx) -> None:
    events = m365_calendar_ctx.last_events or []
    deleted = [e for e in events if e.op == "deleted"]
    target = m365_calendar_ctx.previously_seen_event_id
    assert [e.item_id for e in deleted] == [target], (
        f"expected exactly one deleted event for {target!r}, got {deleted!r}"
    )


@then(parsers.parse("the connector still exposes a persisted delta link for the next cursor"))
def _delta_link_still_exposed(m365_calendar_ctx: _Ctx) -> None:
    connector = m365_calendar_ctx.connector
    assert connector is not None, "When step must run before Then"
    assert connector.last_delta_link, f"expected delta link to remain exposed; got {connector.last_delta_link!r}"
