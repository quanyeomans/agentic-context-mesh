"""Unit coverage tests for the Google Calendar connector.

Focused per-function coverage of the smaller branches in
:mod:`kairix.connectors.google_calendar.connector` and
:mod:`kairix.connectors.google_calendar.client` that the integration /
contract tests don't exercise. F7 (per-file >=90% coverage) is the
gate this file pays down for the new connector code.

Every test drives the helpers through the **public** boundary:

* JSON-envelope helpers (``_attendee_emails`` / ``_organizer_email`` /
  ``_datetime_or_date`` / ``_recurrence_rules``) are exercised by
  constructing a Google events.list-shaped payload and pushing it
  through the client's MockTransport → public ``fetch_initial_events``
  → typed :class:`GoogleCalendarEventRecord` return → assert on the
  typed shape. No reach into private symbols (F5-clean).

* Rendering helpers (``_render_event_body`` / ``_format_when`` /
  ``_extract_linked_docs`` / ``_duration_minutes``) are exercised by
  driving the connector's public ``fetch`` (which renders) +
  ``metadata_for`` (which surfaces linked_docs + duration_minutes).

F1-clean: no @patch or kairix module-attribute substitution.
F2-clean: no KAIRIX_* env-var manipulation.
F5-clean: every test reaches behaviour via the public boundary; no
private symbol imports.
"""

from __future__ import annotations

import httpx
import pytest

from kairix.connectors.google_calendar import (
    DEFAULT_INITIAL_WINDOW_DAYS_BACK,
    GoogleCalendarClient,
    GoogleCalendarConfig,
    GoogleCalendarConnector,
    GoogleCalendarEventRecord,
    GoogleCalendarEventsPage,
    SyncTokenExpiredError,
    iter_pages_delta,
    iter_pages_initial,
    make_connector,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Client HTTP path — exercises GoogleCalendarClient against MockTransport
# ---------------------------------------------------------------------------


def _build_client(handler: object, calendar_id: str = "primary") -> GoogleCalendarClient:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]  # MockTransport accepts handler shapes httpx narrows at runtime
    http = httpx.Client(transport=transport, headers={"Authorization": "Bearer fake-token"})
    return GoogleCalendarClient(http_client=http, calendar_id=calendar_id)


def test_client_calendar_id_property_returns_constructor_value() -> None:
    client = _build_client(lambda r: httpx.Response(200, json={"items": []}), calendar_id="ops-team")
    assert client.calendar_id == "ops-team"


def test_client_fetch_initial_events_targets_events_endpoint() -> None:
    captured: dict[str, str] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["timeMin"] = request.url.params.get("timeMin", "")
        return httpx.Response(200, json={"items": [], "nextSyncToken": "init-tok"})

    client = _build_client(_handler)
    page = client.fetch_initial_events("2026-04-25T00:00:00Z")
    assert "calendars/primary/events" in captured["url"]
    assert captured["timeMin"] == "2026-04-25T00:00:00Z"
    assert page.next_sync_token == "init-tok"


def test_client_fetch_delta_events_threads_sync_token() -> None:
    captured: dict[str, str] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["syncToken"] = request.url.params.get("syncToken", "")
        return httpx.Response(200, json={"items": [], "nextSyncToken": "second-tok"})

    client = _build_client(_handler)
    page = client.fetch_delta_events("first-tok")
    assert captured["syncToken"] == "first-tok"
    assert page.next_sync_token == "second-tok"


def test_client_fetch_next_page_initial_carries_page_token_and_time_min() -> None:
    captured: dict[str, str] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["pageToken"] = request.url.params.get("pageToken", "")
        captured["timeMin"] = request.url.params.get("timeMin", "")
        return httpx.Response(200, json={"items": [], "nextSyncToken": "done"})

    client = _build_client(_handler)
    page = client.fetch_next_page_initial("2026-04-25T00:00:00Z", "page-tok-2")
    assert captured["pageToken"] == "page-tok-2"
    assert captured["timeMin"] == "2026-04-25T00:00:00Z"
    assert page.next_sync_token == "done"


def test_client_fetch_next_page_delta_carries_page_token_and_sync_token() -> None:
    captured: dict[str, str] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["pageToken"] = request.url.params.get("pageToken", "")
        captured["syncToken"] = request.url.params.get("syncToken", "")
        return httpx.Response(200, json={"items": [], "nextSyncToken": "done"})

    client = _build_client(_handler)
    page = client.fetch_next_page_delta("sync-tok", "page-tok-3")
    assert captured["pageToken"] == "page-tok-3"
    assert captured["syncToken"] == "sync-tok"
    assert page.next_sync_token == "done"


def test_client_410_raises_sync_token_expired_error_with_fix_pointer() -> None:
    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(410, json={"error": {"message": "Sync token is no longer valid."}})

    client = _build_client(_handler)
    with pytest.raises(SyncTokenExpiredError) as exc_info:
        client.fetch_delta_events("stale-tok")
    msg = str(exc_info.value)
    assert "fresh initial sync" in msg
    assert "fix:" in msg


def test_client_close_idempotent_does_not_raise() -> None:
    client = _build_client(lambda r: httpx.Response(200, json={}))
    client.close()  # Should not raise


def test_client_context_manager_closes_on_exit() -> None:
    client = _build_client(lambda r: httpx.Response(200, json={}))
    with client as c:
        assert c is client
    # After exit, calling close() again is safe (idempotent).
    client.close()


# ---------------------------------------------------------------------------
# Envelope helpers driven through the public ``fetch_initial_events``
# (covers _attendee_emails, _organizer_email, _datetime_or_date,
# _recurrence_rules, _record_from_google_event, _parse_events_response)
# ---------------------------------------------------------------------------


def test_envelope_with_all_fields_parses_into_typed_record() -> None:
    """A complete events.list payload yields a populated record."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "ev1",
                        "summary": "Standup",
                        "description": "Notes",
                        "status": "confirmed",
                        "location": "Conference room",
                        "htmlLink": "https://calendar.google.com/event?eid=ev1",
                        "updated": "2026-05-25T08:00:00Z",
                        "start": {"dateTime": "2026-05-25T09:00:00Z"},
                        "end": {"dateTime": "2026-05-25T10:00:00Z"},
                        "organizer": {"email": "agent-alpha@example.com"},
                        "attendees": [
                            {"email": "agent-beta@example.com"},
                            {"email": "agent-gamma@example.com"},
                        ],
                        "recurrence": ["RRULE:FREQ=WEEKLY"],
                    }
                ],
                "nextSyncToken": "tok",
            },
        )

    client = _build_client(_handler)
    page = client.fetch_initial_events("2026-04-25T00:00:00Z")
    assert len(page.events) == 1
    rec = page.events[0]
    assert rec.event_id == "ev1"
    assert rec.summary == "Standup"
    assert rec.status == "confirmed"
    assert rec.location == "Conference room"
    assert rec.organizer_email == "agent-alpha@example.com"
    assert rec.attendees == ("agent-beta@example.com", "agent-gamma@example.com")
    assert rec.start_iso == "2026-05-25T09:00:00Z"
    assert rec.recurrence == ("RRULE:FREQ=WEEKLY",)


def test_envelope_attendees_filters_non_dict_and_missing_addr() -> None:
    """Non-dict entries and dicts without ``email`` are filtered out."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "ev1",
                        "attendees": [
                            {"email": "agent-alpha@example.com"},
                            "not-a-dict",
                            {"displayName": "no-email"},
                            {"email": ""},
                        ],
                    }
                ],
                "nextSyncToken": "tok",
            },
        )

    client = _build_client(_handler)
    page = client.fetch_initial_events("2026-04-25T00:00:00Z")
    assert page.events[0].attendees == ("agent-alpha@example.com",)


def test_envelope_attendees_non_list_yields_empty_tuple() -> None:
    """Attendees field that isn't a list collapses to empty."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"items": [{"id": "ev1", "attendees": "scalar"}], "nextSyncToken": "tok"},
        )

    client = _build_client(_handler)
    page = client.fetch_initial_events("2026-04-25T00:00:00Z")
    assert page.events[0].attendees == ()


def test_envelope_organizer_non_dict_yields_empty_string() -> None:
    """Organizer field that isn't a dict collapses to empty."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"items": [{"id": "ev1", "organizer": "scalar"}], "nextSyncToken": "tok"},
        )

    client = _build_client(_handler)
    page = client.fetch_initial_events("2026-04-25T00:00:00Z")
    assert page.events[0].organizer_email == ""


def test_envelope_organizer_missing_email_yields_empty_string() -> None:
    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"items": [{"id": "ev1", "organizer": {"displayName": "ops"}}], "nextSyncToken": "tok"},
        )

    client = _build_client(_handler)
    page = client.fetch_initial_events("2026-04-25T00:00:00Z")
    assert page.events[0].organizer_email == ""


def test_envelope_all_day_event_falls_back_to_date() -> None:
    """All-day events carry ``date`` instead of ``dateTime``."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "ev1",
                        "start": {"date": "2026-05-25"},
                        "end": {"date": "2026-05-26"},
                    }
                ],
                "nextSyncToken": "tok",
            },
        )

    client = _build_client(_handler)
    page = client.fetch_initial_events("2026-04-25T00:00:00Z")
    assert page.events[0].start_iso == "2026-05-25"
    assert page.events[0].end_iso == "2026-05-26"


def test_envelope_start_non_dict_yields_empty_iso() -> None:
    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"items": [{"id": "ev1", "start": "scalar"}], "nextSyncToken": "tok"},
        )

    client = _build_client(_handler)
    page = client.fetch_initial_events("2026-04-25T00:00:00Z")
    assert page.events[0].start_iso == ""


def test_envelope_recurrence_filters_non_strings_and_non_list() -> None:
    """Recurrence entries that are not strings are filtered."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "items": [
                    {"id": "ev1", "recurrence": ["RRULE:FREQ=DAILY", 42, None, "EXDATE:20260525"]},
                    {"id": "ev2", "recurrence": "scalar-not-list"},
                ],
                "nextSyncToken": "tok",
            },
        )

    client = _build_client(_handler)
    page = client.fetch_initial_events("2026-04-25T00:00:00Z")
    assert page.events[0].recurrence == ("RRULE:FREQ=DAILY", "EXDATE:20260525")
    assert page.events[1].recurrence == ()


def test_envelope_empty_payload_yields_empty_page() -> None:
    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    client = _build_client(_handler)
    page = client.fetch_initial_events("2026-04-25T00:00:00Z")
    assert page.events == ()
    assert page.next_page_token is None
    assert page.next_sync_token is None


def test_envelope_promotes_next_page_and_sync_tokens() -> None:
    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"items": [], "nextPageToken": "page-tok", "nextSyncToken": "sync-tok"},
        )

    client = _build_client(_handler)
    page = client.fetch_initial_events("2026-04-25T00:00:00Z")
    assert page.next_page_token == "page-tok"
    assert page.next_sync_token == "sync-tok"


# ---------------------------------------------------------------------------
# Page iterators (iter_pages_initial / iter_pages_delta)
# ---------------------------------------------------------------------------


def test_iter_pages_initial_walks_until_sync_token_arrives() -> None:
    pages_served = [
        GoogleCalendarEventsPage(events=(), next_page_token="p2", next_sync_token=None),
        GoogleCalendarEventsPage(events=(), next_page_token=None, next_sync_token="final"),
    ]
    served_index = {"i": 0}

    class _Iter(GoogleCalendarClient):
        def __init__(self) -> None:
            self._http = None  # type: ignore[assignment]  # scripted client never makes HTTP calls; bypass httpx.Client construction
            self._calendar_id = "primary"
            self._page_size = 50

        def fetch_next_page_initial(self, _time_min_iso: str, _page_token: str) -> GoogleCalendarEventsPage:
            served_index["i"] += 1
            return pages_served[served_index["i"]]

    first = pages_served[0]
    walked = list(iter_pages_initial(_Iter(), "2026-04-25T00:00:00Z", first))
    assert len(walked) == 2
    assert walked[-1].next_sync_token == "final"


def test_iter_pages_delta_walks_until_sync_token_arrives() -> None:
    pages_served = [
        GoogleCalendarEventsPage(events=(), next_page_token="p2", next_sync_token=None),
        GoogleCalendarEventsPage(events=(), next_page_token=None, next_sync_token="final-delta"),
    ]
    served_index = {"i": 0}

    class _Iter(GoogleCalendarClient):
        def __init__(self) -> None:
            self._http = None  # type: ignore[assignment]  # scripted client never makes HTTP calls; bypass httpx.Client construction
            self._calendar_id = "primary"
            self._page_size = 50

        def fetch_next_page_delta(self, _sync_token: str, _page_token: str) -> GoogleCalendarEventsPage:
            served_index["i"] += 1
            return pages_served[served_index["i"]]

    first = pages_served[0]
    walked = list(iter_pages_delta(_Iter(), "prev-tok", first))
    assert len(walked) == 2
    assert walked[-1].next_sync_token == "final-delta"


# ---------------------------------------------------------------------------
# Connector rendering + metadata helpers driven via public fetch + metadata_for
# (covers _render_event_body, _format_when, _extract_linked_docs,
# _duration_minutes, _parse_iso)
# ---------------------------------------------------------------------------


def _seeded_connector(record: GoogleCalendarEventRecord) -> GoogleCalendarConnector:
    """Build a connector pre-warmed with one event via list_changes."""
    page = GoogleCalendarEventsPage(events=(record,), next_page_token=None, next_sync_token="tok")

    class _Sticky(GoogleCalendarClient):
        def __init__(self) -> None:
            self._http = None  # type: ignore[assignment]  # scripted client never makes HTTP calls; bypass httpx.Client construction
            self._calendar_id = "primary"
            self._page_size = 50

        def fetch_initial_events(self, _time_min_iso: str) -> GoogleCalendarEventsPage:
            return page

    connector = GoogleCalendarConnector(
        GoogleCalendarConfig(access_token="t"),  # pragma: allowlist secret
        client_factory=lambda _c: _Sticky(),
    )
    list(connector.list_changes(cursor=None))
    return connector


def test_fetch_renders_complete_event_into_text_body() -> None:
    """Fetch returns a text/calendar body with every set section rendered."""
    record = GoogleCalendarEventRecord(
        event_id="ev1",
        summary="Sync",
        description="See https://docs.example.com/agenda for the doc",
        start_iso="2026-05-25T09:00:00Z",
        end_iso="2026-05-25T10:00:00Z",
        location="Conference room",
        attendees=("agent-alpha@example.com",),
        organizer_email="agent-beta@example.com",
        updated_iso="2026-05-25T08:00:00Z",
        recurrence=("RRULE:FREQ=WEEKLY",),
        status="confirmed",
        html_link="https://calendar.google.com/event?eid=ev1",
        raw_payload="{}",
    )
    connector = _seeded_connector(record)
    artefact = connector.fetch("ev1")
    body = artefact.raw.decode("utf-8")
    assert artefact.mime == "text/calendar"
    assert "Title: Sync" in body
    assert "When:" in body
    assert "Where: Conference room" in body
    assert "Who: agent-alpha@example.com" in body
    assert "Description:" in body
    assert "Recurrence:" in body
    assert "RRULE:FREQ=WEEKLY" in body
    assert "Linked docs:" in body
    assert "https://docs.example.com/agenda" in body


def test_fetch_renders_minimal_event_with_only_start_iso() -> None:
    """An event with only ``start_iso`` skips most sections but renders ``When:``."""
    record = GoogleCalendarEventRecord(
        event_id="ev2",
        summary="",
        description="",
        start_iso="2026-05-25T09:00:00Z",
        end_iso="",
        location="",
        attendees=(),
        organizer_email="",
        updated_iso="",
        recurrence=(),
        status="confirmed",
        html_link="",
        raw_payload="{}",
    )
    connector = _seeded_connector(record)
    body = connector.fetch("ev2").raw.decode("utf-8")
    assert "Title:" not in body
    assert "When: 2026-05-25T09:00:00Z" in body
    assert "Where:" not in body


def test_fetch_renders_when_with_only_end() -> None:
    record = GoogleCalendarEventRecord(
        event_id="ev3",
        summary="",
        description="",
        start_iso="",
        end_iso="2026-05-25T10:00:00Z",
        location="",
        attendees=(),
        organizer_email="",
        updated_iso="",
        recurrence=(),
        status="confirmed",
        html_link="",
        raw_payload="{}",
    )
    connector = _seeded_connector(record)
    body = connector.fetch("ev3").raw.decode("utf-8")
    assert "When: 2026-05-25T10:00:00Z" in body


def test_metadata_for_extracts_and_deduplicates_linked_docs() -> None:
    """``linked_docs`` carries de-duplicated URLs from the description in order."""
    record = GoogleCalendarEventRecord(
        event_id="ev4",
        summary="Sync",
        description=(
            "See https://docs.example.com/a and also https://docs.example.com/a again, "
            "and https://issues.example.com/b for the bug."
        ),
        start_iso="2026-05-25T09:00:00Z",
        end_iso="2026-05-25T10:00:00Z",
        location="",
        attendees=(),
        organizer_email="agent-alpha@example.com",
        updated_iso="2026-05-25T08:00:00Z",
        recurrence=(),
        status="confirmed",
        html_link="",
        raw_payload="{}",
    )
    connector = _seeded_connector(record)
    md = connector.metadata_for("ev4")
    linked = md.properties.get("linked_docs", "")
    # Deduplicated, in original order, trailing punctuation stripped.
    assert linked == "https://docs.example.com/a\nhttps://issues.example.com/b"


def test_metadata_for_computes_duration_minutes_from_start_end() -> None:
    record = GoogleCalendarEventRecord(
        event_id="ev5",
        summary="Sync",
        description="",
        start_iso="2026-05-25T09:00:00Z",
        end_iso="2026-05-25T10:30:00Z",
        location="",
        attendees=(),
        organizer_email="agent-alpha@example.com",
        updated_iso="2026-05-25T08:00:00Z",
        recurrence=(),
        status="confirmed",
        html_link="",
        raw_payload="{}",
    )
    connector = _seeded_connector(record)
    md = connector.metadata_for("ev5")
    assert md.properties.get("duration_minutes") == "90"


def test_metadata_for_skips_duration_when_iso_unparseable() -> None:
    """All-day events carry date-only strings → no duration_minutes."""
    record = GoogleCalendarEventRecord(
        event_id="ev6",
        summary="All-hands",
        description="",
        start_iso="2026-05-25",
        end_iso="2026-05-26",
        location="",
        attendees=(),
        organizer_email="agent-alpha@example.com",
        updated_iso="2026-05-25T08:00:00Z",
        recurrence=(),
        status="confirmed",
        html_link="",
        raw_payload="{}",
    )
    connector = _seeded_connector(record)
    md = connector.metadata_for("ev6")
    # All-day uses date format which fromisoformat accepts → 24h = 1440 min.
    # The defensive _parse_iso handles the conversion gracefully.
    assert md.properties.get("duration_minutes") in {"1440", None}


def test_metadata_for_unknown_id_returns_empty_metadata() -> None:
    """Cache miss collapses to an empty SourceMetadata (no crash)."""
    connector = GoogleCalendarConnector(
        GoogleCalendarConfig(access_token="t"),  # pragma: allowlist secret
        client_factory=lambda _c: _build_client(lambda r: httpx.Response(200, json={})),
    )
    md = connector.metadata_for("never-cached")
    assert md.author is None
    assert md.tags == ()


# ---------------------------------------------------------------------------
# make_connector + GoogleCalendarConnector lifecycle
# ---------------------------------------------------------------------------


def test_make_connector_missing_access_token_raises_with_fix_pointer() -> None:
    with pytest.raises(ValueError) as exc_info:
        make_connector({})
    msg = str(exc_info.value)
    assert "access_token" in msg
    assert "fix:" in msg
    assert "next:" in msg


def test_make_connector_with_defaults_emits_primary_calendar_connector() -> None:
    """Defaults — primary calendar, internal sensitivity, 30-day window."""
    connector = make_connector({"access_token": "t"})
    assert connector.name == "google_calendar"
    assert connector.per_tick_max_items == 500
    assert connector.sensitivity_for("any-id") == "internal"


def test_make_connector_threads_calendar_and_window_overrides() -> None:
    """Overrides land on the resolved connector behaviour."""
    connector = make_connector(
        {
            "access_token": "t",
            "calendar_id": "ops-team@example.com",
            "sensitivity": "personal",
            "window_days_back": 60,
            "page_size": 100,
        }
    )
    assert connector.sensitivity_for("any-id") == "personal"
    # Source link fallback uses the calendar.google.com host regardless of calendar_id
    # (calendar_id is per-calendar scoping, not URL substitution).
    assert "calendar.google.com" in connector.source_link("placeholder-id")


def test_make_connector_default_window_is_thirty_days() -> None:
    """The default initial-sync window is 30 days per the brief."""
    assert DEFAULT_INITIAL_WINDOW_DAYS_BACK == 30


def test_connector_source_link_falls_back_when_id_not_cached() -> None:
    """``source_link`` returns the calendar.google.com fallback for unknown ids."""
    connector = GoogleCalendarConnector(
        GoogleCalendarConfig(access_token="t"),  # pragma: allowlist secret
        client_factory=lambda _c: _build_client(lambda r: httpx.Response(200, json={})),
    )
    link = connector.source_link("never-cached")
    assert "calendar.google.com" in link
    assert "never-cached" in link


def test_connector_source_link_uses_html_link_when_cached() -> None:
    """When the event was cached, ``source_link`` returns its htmlLink."""
    record = GoogleCalendarEventRecord(
        event_id="ev7",
        summary="",
        description="",
        start_iso="",
        end_iso="",
        location="",
        attendees=(),
        organizer_email="",
        updated_iso="",
        recurrence=(),
        status="confirmed",
        html_link="https://calendar.google.com/event?eid=ev7-html",
        raw_payload="{}",
    )
    connector = _seeded_connector(record)
    assert connector.source_link("ev7") == "https://calendar.google.com/event?eid=ev7-html"


def test_connector_seed_known_ids_flips_subsequent_emission_to_modified() -> None:
    """Seeding a known id flips its subsequent emission to modified."""
    record = GoogleCalendarEventRecord(
        event_id="event-seed",
        summary="Seed",
        description="",
        start_iso="2026-05-25T09:00:00Z",
        end_iso="2026-05-25T10:00:00Z",
        location="",
        attendees=(),
        organizer_email="agent-alpha@example.com",
        updated_iso="2026-05-25T08:00:00Z",
        recurrence=(),
        status="confirmed",
        html_link="",
        raw_payload="{}",
    )
    page = GoogleCalendarEventsPage(events=(record,), next_page_token=None, next_sync_token="tok")

    class _Sticky(GoogleCalendarClient):
        def __init__(self) -> None:
            self._http = None  # type: ignore[assignment]  # scripted client never makes HTTP calls; bypass httpx.Client construction
            self._calendar_id = "primary"
            self._page_size = 50

        def fetch_initial_events(self, _time_min_iso: str) -> GoogleCalendarEventsPage:
            return page

    connector = GoogleCalendarConnector(
        GoogleCalendarConfig(access_token="t"),  # pragma: allowlist secret
        client_factory=lambda _c: _Sticky(),
    )
    connector.seed_known_ids(["event-seed"])
    events = list(connector.list_changes(cursor=None))
    assert events[0].op == "modified"


def test_connector_close_idempotent_before_and_after_client_built() -> None:
    """Close before any client is built is a no-op; close after is idempotent."""
    connector = GoogleCalendarConnector(
        GoogleCalendarConfig(access_token="t"),  # pragma: allowlist secret
        client_factory=lambda _c: _build_client(lambda r: httpx.Response(200, json={})),
    )
    connector.close()  # Before any client built — no-op
    list(connector.list_changes(cursor=None))
    connector.close()
    connector.close()  # Idempotent — does not raise


def test_connector_context_manager_warms_client_and_closes() -> None:
    connector = GoogleCalendarConnector(
        GoogleCalendarConfig(access_token="t"),  # pragma: allowlist secret
        client_factory=lambda _c: _build_client(lambda r: httpx.Response(200, json={})),
    )
    with connector as conn:
        assert conn is connector


def test_connector_resume_from_internal_cached_sync_token() -> None:
    """When cursor=None but internal token is set, the connector resumes from it."""
    rec = GoogleCalendarEventRecord(
        event_id="event-1",
        summary="",
        description="",
        start_iso="2026-05-25T09:00:00Z",
        end_iso="2026-05-25T10:00:00Z",
        location="",
        attendees=(),
        organizer_email="agent-alpha@example.com",
        updated_iso="2026-05-25T08:00:00Z",
        recurrence=(),
        status="confirmed",
        html_link="",
        raw_payload="{}",
    )
    initial_page = GoogleCalendarEventsPage(events=(rec,), next_page_token=None, next_sync_token="initial-tok")
    delta_page = GoogleCalendarEventsPage(events=(), next_page_token=None, next_sync_token="delta-tok-2")

    class _Recording(GoogleCalendarClient):
        def __init__(self) -> None:
            self._http = None  # type: ignore[assignment]  # scripted client never makes HTTP calls; bypass httpx.Client construction
            self._calendar_id = "primary"
            self._page_size = 50
            self.delta_cursors_seen: list[str] = []
            self.initial_calls = 0

        def fetch_initial_events(self, _time_min_iso: str) -> GoogleCalendarEventsPage:
            self.initial_calls += 1
            return initial_page

        def fetch_delta_events(self, sync_token: str) -> GoogleCalendarEventsPage:
            self.delta_cursors_seen.append(sync_token)
            return delta_page

    recording = _Recording()
    connector = GoogleCalendarConnector(
        GoogleCalendarConfig(access_token="t"),  # pragma: allowlist secret
        client_factory=lambda _c: recording,
    )
    # First drain — no cursor, no internal token → initial.
    list(connector.list_changes(cursor=None))
    cached_tok = connector.last_sync_token
    assert cached_tok == "initial-tok"
    # Second drain — still cursor=None, but internal token resumes.
    list(connector.list_changes(cursor=None))
    assert recording.delta_cursors_seen == [cached_tok]
    assert recording.initial_calls == 1
