"""F68 failure-injection coverage for the Google Calendar connector.

ADR-024 Bundle A: every Protocol method has at least one failure-mode
contract test from the catalogue ``{raises, times_out, returns_partial,
returns_empty, unauthorized, unavailable}``. This module covers the
Google Calendar Protocol surface:

* :meth:`GoogleCalendarConnector.list_changes` — ``raises`` (Google
  500 surfaces a typed :class:`HTTPStatusError`); ``returns_empty``
  (no events in window); ``unauthorized`` (401 surfaces a typed
  :class:`HTTPStatusError`).
* :meth:`GoogleCalendarConnector.fetch` — ``raises`` on a never-seen
  id (cache miss raises with an actionable fix pointer).
* :meth:`GoogleCalendarConnector.list_changes` — ``returns_partial``:
  cancelled events are filtered out; only confirmed events surface.
* :meth:`GoogleCalendarConnector.list_changes` — sync-token recovery
  on 410 Gone is exercised end-to-end through the connector (not just
  the client).

F47 — every test reaches the production code via the connector's
constructor; no direct ``*Pipeline(...)`` construction.
"""

from __future__ import annotations

import httpx
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

pytestmark = pytest.mark.integration


def _record(event_id: str, status: str = "confirmed") -> GoogleCalendarEventRecord:
    return GoogleCalendarEventRecord(
        event_id=event_id,
        summary=f"Event {event_id}",
        description="",
        start_iso="2026-05-25T09:00:00Z",
        end_iso="2026-05-25T10:00:00Z",
        location="",
        attendees=(),
        organizer_email="agent-alpha@example.com",
        updated_iso="2026-05-25T08:00:00Z",
        recurrence=(),
        status=status,
        html_link=f"https://calendar.google.com/event?eid={event_id}",
        raw_payload="{}",
    )


class _ScriptedClient(GoogleCalendarClient):
    """Scripted client whose behaviour the test pins via constructor flags."""

    def __init__(
        self,
        *,
        initial_page: GoogleCalendarEventsPage | None = None,
        raise_initial: Exception | None = None,
        raise_delta: Exception | None = None,
        delta_page: GoogleCalendarEventsPage | None = None,
    ) -> None:
        self._calendar_id = "primary"
        self._http = None  # type: ignore[assignment]  # scripted client never makes HTTP calls; bypass httpx.Client construction
        self._page_size = 50
        self._initial = initial_page
        self._raise_initial = raise_initial
        self._raise_delta = raise_delta
        self._delta = delta_page
        self.initial_calls = 0
        self.delta_calls = 0

    def fetch_initial_events(self, _time_min_iso: str) -> GoogleCalendarEventsPage:
        self.initial_calls += 1
        if self._raise_initial is not None:
            raise self._raise_initial
        assert self._initial is not None, "test must seed initial_page"
        return self._initial

    def fetch_delta_events(self, _sync_token: str) -> GoogleCalendarEventsPage:
        self.delta_calls += 1
        if self._raise_delta is not None:
            raise self._raise_delta
        assert self._delta is not None, "test must seed delta_page"
        return self._delta

    def fetch_next_page_initial(self, _time_min_iso: str, _page_token: str) -> GoogleCalendarEventsPage:
        return self.fetch_initial_events("")

    def fetch_next_page_delta(self, _sync_token: str, _page_token: str) -> GoogleCalendarEventsPage:
        return self.fetch_delta_events("")

    def close(self) -> None:
        # Intentionally empty — scripted client owns no resources.
        return None


def _build_connector(client: _ScriptedClient) -> GoogleCalendarConnector:
    return GoogleCalendarConnector(
        GoogleCalendarConfig(access_token="t"),  # pragma: allowlist secret — test fixture
        client_factory=lambda _c: client,
    )


# ---------------------------------------------------------------------------
# `raises` — typed HTTP error surfaces through list_changes
# ---------------------------------------------------------------------------


def test_list_changes_500_raises_typed_http_error() -> None:
    """Google 500 escapes list_changes as :class:`HTTPStatusError`.

    Sabotage proof: wrap the ``raise`` in :meth:`_drain` with a
    ``try / except / pass`` block — re-run, the
    ``pytest.raises`` block fails because list_changes returns an
    empty iterator instead of propagating the error.
    """
    fake_500 = httpx.HTTPStatusError(
        "google 500",
        request=httpx.Request("GET", "https://www.googleapis.com/calendar/v3/calendars/primary/events"),
        response=httpx.Response(500, request=httpx.Request("GET", "https://x.example/")),
    )
    client = _ScriptedClient(raise_initial=fake_500)
    connector = _build_connector(client)

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        list(connector.list_changes(cursor=None))
    assert exc_info.value.response.status_code == 500


def test_list_changes_401_raises_typed_http_error() -> None:
    """Google 401 unauthorized escapes as :class:`HTTPStatusError`.

    F68 ``unauthorized`` shape — covers credential-expiry mid-tick.

    Sabotage proof: catch the 401 silently in :meth:`_drain` — re-run,
    the ``pytest.raises`` block fails.
    """
    fake_401 = httpx.HTTPStatusError(
        "google 401",
        request=httpx.Request("GET", "https://www.googleapis.com/calendar/v3/calendars/primary/events"),
        response=httpx.Response(401, request=httpx.Request("GET", "https://x.example/")),
    )
    client = _ScriptedClient(raise_initial=fake_401)
    connector = _build_connector(client)

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        list(connector.list_changes(cursor=None))
    assert exc_info.value.response.status_code == 401


# ---------------------------------------------------------------------------
# `returns_empty` — empty events array is normal, not an error
# ---------------------------------------------------------------------------


def test_list_changes_empty_window_yields_no_events() -> None:
    """An empty events array surfaces as zero ChangeEvents and a fresh syncToken.

    Sabotage proof: change the loop in :meth:`_absorb_page` to skip the
    ``next_sync_token`` assignment when the page has no events — re-run,
    the ``next_cursor`` assertion fails.
    """
    empty_page = GoogleCalendarEventsPage(events=(), next_page_token=None, next_sync_token="fresh-tok")
    client = _ScriptedClient(initial_page=empty_page)
    connector = _build_connector(client)

    events = list(connector.list_changes(cursor=None))
    assert events == [], f"empty window must surface zero events; got {events!r}"
    assert connector.next_cursor() == "fresh-tok", (
        f"empty window must still advance the cursor; got {connector.next_cursor()!r}"
    )


# ---------------------------------------------------------------------------
# `returns_partial` — cancelled events are filtered out
# ---------------------------------------------------------------------------


def test_list_changes_cancelled_events_are_skipped() -> None:
    """Per ADR-028: ``status='cancelled'`` events do not surface as ChangeEvents.

    Sabotage proof: remove the ``if record.status == "cancelled": return None``
    guard in :meth:`_record_to_change_event`. Re-run: the test fails
    because the cancelled event also surfaces.
    """
    page = GoogleCalendarEventsPage(
        events=(
            _record("event-confirmed"),
            _record("event-cancelled", status="cancelled"),
        ),
        next_page_token=None,
        next_sync_token="tok",
    )
    client = _ScriptedClient(initial_page=page)
    connector = _build_connector(client)

    events = list(connector.list_changes(cursor=None))
    item_ids = [e.item_id for e in events]
    assert item_ids == ["event-confirmed"], f"cancelled events must be skipped; got {item_ids!r}"


# ---------------------------------------------------------------------------
# fetch raises with an actionable fix pointer on cache miss
# ---------------------------------------------------------------------------


def test_fetch_unknown_item_id_raises_with_fix_pointer() -> None:
    """Calling fetch with an id the connector hasn't seen raises ValueError with a fix pointer.

    Sabotage proof: swap the ValueError for a silent ``return RawArtefact(raw=b"", ...)`` —
    the ``pytest.raises`` block fails.
    """
    page = GoogleCalendarEventsPage(events=(), next_page_token=None, next_sync_token="tok")
    client = _ScriptedClient(initial_page=page)
    connector = _build_connector(client)

    with pytest.raises(ValueError) as exc_info:
        connector.fetch("never-seen-event")

    message = str(exc_info.value)
    assert "no cached event" in message
    assert "fix:" in message, f"F21 affordance: error must carry a fix pointer; got {message!r}"


# ---------------------------------------------------------------------------
# 410 sync-token recovery — connector transparently falls back to initial sync
# ---------------------------------------------------------------------------


def test_list_changes_410_sync_token_recovers_via_initial_sync() -> None:
    """A 410 on delta transparently falls back to a fresh initial sync.

    The client raises :class:`SyncTokenExpiredError`; the connector
    catches it and re-runs against :meth:`fetch_initial_events` so
    upstream sees a normal event stream rather than the exception.

    Sabotage proof: remove the ``except SyncTokenExpiredError:`` block
    in :meth:`_drain`. Re-run: the exception escapes list_changes
    instead of being recovered.
    """
    from kairix.connectors.google_calendar.client import SyncTokenExpiredError

    initial_page = GoogleCalendarEventsPage(
        events=(_record("event-recovered"),),
        next_page_token=None,
        next_sync_token="fresh-tok",
    )

    class _RecoveringClient(_ScriptedClient):
        """Client that raises on delta, then serves initial on retry."""

        def __init__(self) -> None:
            super().__init__(initial_page=initial_page)

        def fetch_delta_events(self, _sync_token: str) -> GoogleCalendarEventsPage:
            self.delta_calls += 1
            raise SyncTokenExpiredError("syncToken expired; fresh initial sync required")

    client = _RecoveringClient()
    connector = _build_connector(client)

    events = list(connector.list_changes(cursor="stale-token"))
    assert client.delta_calls == 1, "delta call must have fired exactly once"
    assert client.initial_calls == 1, "initial fallback must have fired exactly once"
    item_ids = [e.item_id for e in events]
    assert item_ids == ["event-recovered"], f"410 must recover transparently with the initial page; got {item_ids!r}"
    assert connector.next_cursor() == "fresh-tok", (
        f"recovery must advance to the fresh cursor; got {connector.next_cursor()!r}"
    )
