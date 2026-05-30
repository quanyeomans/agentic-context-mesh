"""Thin httpx wrapper around Google Calendar API v3 for event sync.

Wraps ``events.list`` with two distinct entry points:

* ``fetch_initial_events(time_min)`` — bootstrap a fresh syncToken by
  pulling events from ``time_min`` (typically ``now - 30 days``) until
  Google returns a ``nextSyncToken``.
* ``fetch_delta_events(sync_token)`` — incremental sync using the
  persisted ``nextSyncToken``. Per Google's docs a 410 ``Gone``
  response indicates the token is too old; callers must catch
  :class:`SyncTokenExpiredError` and fall back to a fresh initial
  sync.

Per the architecture's three-layer split (docs/architecture/
provider-plugin-architecture.md, mirrored for connectors by F35), this
module ONLY imports from:

* :mod:`httpx` (third-party transport)

It does NOT import from ``kairix.transport``, ``kairix.providers``,
``kairix.core.connectors``, or any sibling ``kairix.connectors.*``.

Per F42, the wrapper's public methods return frozen dataclasses
(:class:`GoogleCalendarEventRecord`, :class:`GoogleCalendarEventsPage`)
or tuples of them — never bare ``dict[str, Any]`` — so the
connector's Protocol boundary is typed.

F15-clean: this module never logs bearer tokens or other credentials.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx

# Google Calendar API base URL. The connector targets the v3 surface
# (events.list with syncToken is GA there).
CALENDAR_API_BASE_URL = "https://www.googleapis.com/calendar/v3"

# Default page size for events.list. Google caps at 2500 server-side;
# 250 keeps round-trips small while still draining a typical calendar
# in one page.
DEFAULT_PAGE_SIZE = 250

# F17 — query-parameter keys recur across the four fetch methods;
# extracting to module constants pins the spelling once and lets
# the reader find every call site by symbol search instead of grep.
_PARAM_MAX_RESULTS = "maxResults"
_PARAM_SHOW_DELETED = "showDeleted"
_PARAM_TIME_MIN = "timeMin"
_PARAM_SINGLE_EVENTS = "singleEvents"
_PARAM_PAGE_TOKEN = "pageToken"  # noqa: S105 — Google Calendar API query-string key name, not a credential value
_PARAM_SYNC_TOKEN = "syncToken"  # noqa: S105 — Google Calendar API query-string key name, not a credential value


class SyncTokenExpiredError(Exception):
    """Raised when Google returns 410 Gone on a syncToken-based request.

    Per Google's docs (developers.google.com/calendar/api/guides/sync),
    a 410 response on an incremental sync means the syncToken is too
    old; the caller MUST discard the token and run a fresh full sync
    from a time-window. Connectors handle this by retrying once
    through :meth:`GoogleCalendarClient.fetch_initial_events`.
    """


@dataclass(frozen=True)
class GoogleCalendarEventRecord:
    """One calendar event as surfaced by Google Calendar API v3.

    Frozen-dataclass return type per F42. Carries the minimum fields
    the connector needs to emit a ``ChangeEvent`` and populate entity
    signals (attendees -> Person nodes) downstream. The full Google
    payload is preserved on :attr:`raw_payload` for Bronze persistence.

    ``status`` mirrors the Google ``status`` field (``confirmed`` /
    ``tentative`` / ``cancelled``). The connector skips cancelled
    events per the brief — they do not produce ``ChangeEvent``s.

    ``recurrence`` carries the RRULE strings from Google's
    ``recurrence`` field for master recurring events. The connector
    surfaces these on ``SourceMetadata.properties.recurrence_rule``
    instead of expanding occurrences (per ADR-028).
    """

    event_id: str
    summary: str
    description: str
    start_iso: str
    end_iso: str
    location: str
    attendees: tuple[str, ...]
    organizer_email: str
    updated_iso: str
    recurrence: tuple[str, ...]
    status: str
    html_link: str
    raw_payload: str


@dataclass(frozen=True)
class GoogleCalendarEventsPage:
    """One page of an events.list response.

    ``events`` is the records on this page; ``next_page_token`` (if
    set) is the token for the next page within the same sync;
    ``next_sync_token`` (if set) is the cursor to persist for the next
    incremental tick. Exactly one of the two link fields is set on
    each Google response per the events.list contract.
    """

    events: tuple[GoogleCalendarEventRecord, ...]
    next_page_token: str | None
    next_sync_token: str | None


class GoogleCalendarClient:
    """Narrow wrapper around the Google Calendar events.list surface.

    Construction is cheap — no HTTP at __init__. The first
    :meth:`fetch_initial_events` (or :meth:`fetch_delta_events`) call
    triggers the OAuth bearer-token attachment through the configured
    ``http_client``.

    DI seams:

    * ``http_client`` — :class:`httpx.Client` instance carrying the
      OAuth Bearer token via httpx auth. Tests inject a pre-configured
      ``MockTransport`` so no real network I/O fires.
    * ``calendar_id`` — defaults to ``"primary"``; operator can scope
      to a specific calendar id.
    * ``page_size`` — overrides the events.list ``maxResults``
      parameter.
    """

    def __init__(
        self,
        http_client: httpx.Client,
        calendar_id: str = "primary",
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> None:
        self._http = http_client
        self._calendar_id = calendar_id
        self._page_size = page_size

    @property
    def calendar_id(self) -> str:
        return self._calendar_id

    def fetch_initial_events(self, time_min_iso: str) -> GoogleCalendarEventsPage:
        """First-time sync — pull events from ``time_min_iso`` onward.

        Returns one page of :class:`GoogleCalendarEventRecord` plus a
        ``next_page_token`` and/or ``next_sync_token`` for follow-up.
        The caller drains all pages by calling
        :meth:`fetch_next_page_initial` in a loop until
        ``next_sync_token`` is set.

        ``time_min_iso`` must be an ISO-8601 UTC timestamp. The
        connector picks the window (default: 30 days back; see
        :data:`kairix.connectors.google_calendar.connector.DEFAULT_INITIAL_WINDOW_DAYS_BACK`).
        """
        params: dict[str, Any] = {
            _PARAM_TIME_MIN: time_min_iso,
            _PARAM_MAX_RESULTS: self._page_size,
            _PARAM_SINGLE_EVENTS: "false",
            _PARAM_SHOW_DELETED: "true",
        }
        return self._get_page(params)

    def fetch_next_page_initial(self, time_min_iso: str, page_token: str) -> GoogleCalendarEventsPage:
        """Follow a ``nextPageToken`` during the initial window walk.

        Google requires the SAME query parameters across the page walk;
        the only addition is ``pageToken``. The caller threads
        ``time_min_iso`` through unchanged so the page walk lands on
        the same window the initial call started.
        """
        params: dict[str, Any] = {
            _PARAM_TIME_MIN: time_min_iso,
            _PARAM_MAX_RESULTS: self._page_size,
            _PARAM_SINGLE_EVENTS: "false",
            _PARAM_SHOW_DELETED: "true",
            _PARAM_PAGE_TOKEN: page_token,
        }
        return self._get_page(params)

    def fetch_delta_events(self, sync_token: str) -> GoogleCalendarEventsPage:
        """Incremental sync using a persisted ``nextSyncToken``.

        Raises :class:`SyncTokenExpiredError` on 410 Gone; the caller
        MUST recover by discarding the token and calling
        :meth:`fetch_initial_events`.
        """
        params: dict[str, Any] = {
            _PARAM_SYNC_TOKEN: sync_token,
            _PARAM_MAX_RESULTS: self._page_size,
            _PARAM_SHOW_DELETED: "true",
        }
        return self._get_page(params)

    def fetch_next_page_delta(self, sync_token: str, page_token: str) -> GoogleCalendarEventsPage:
        """Follow a ``nextPageToken`` during an incremental drain.

        Mirrors :meth:`fetch_next_page_initial` for the delta path.
        ``sync_token`` is threaded unchanged per Google's contract.
        """
        params: dict[str, Any] = {
            _PARAM_SYNC_TOKEN: sync_token,
            _PARAM_MAX_RESULTS: self._page_size,
            _PARAM_SHOW_DELETED: "true",
            _PARAM_PAGE_TOKEN: page_token,
        }
        return self._get_page(params)

    def close(self) -> None:
        """Close the underlying :class:`httpx.Client`."""
        self._http.close()

    def __enter__(self) -> GoogleCalendarClient:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _get_page(self, params: dict[str, Any]) -> GoogleCalendarEventsPage:
        """Issue one events.list GET and translate to a typed page.

        Maps Google's 410 Gone -> :class:`SyncTokenExpiredError` per
        the docs (developers.google.com/calendar/api/guides/sync).
        All other 4xx / 5xx surface as the typed
        :class:`httpx.HTTPStatusError`; the worker's dead-letter path
        catches it explicitly per F68 / ADR-024.
        """
        url = f"{CALENDAR_API_BASE_URL}/calendars/{self._calendar_id}/events"
        response = self._http.get(url, params=params)
        if response.status_code == 410:
            raise SyncTokenExpiredError(
                "google_calendar: syncToken expired (410 Gone). "
                "fix: discard the persisted nextSyncToken and run a fresh initial sync. "
                "next: see https://developers.google.com/calendar/api/guides/sync."
            )
        response.raise_for_status()
        return _parse_events_response(response.json())


def _parse_events_response(payload: dict[str, Any]) -> GoogleCalendarEventsPage:
    """Translate a Google events.list JSON payload to a typed page.

    Google's events.list payload looks like::

        {
          "kind": "calendar#events",
          "nextPageToken": "..." | absent,
          "nextSyncToken": "..." | absent,
          "items": [ { event payload }, ... ]
        }

    Cancelled events (status="cancelled") surface as records with
    ``status="cancelled"``; the connector decides whether to emit
    them as ChangeEvents (it does not, per the brief).
    """
    records: list[GoogleCalendarEventRecord] = []
    for item in payload.get("items", []):
        records.append(_record_from_google_event(item))

    next_page = payload.get("nextPageToken")
    next_sync = payload.get("nextSyncToken")
    return GoogleCalendarEventsPage(
        events=tuple(records),
        next_page_token=str(next_page) if isinstance(next_page, str) else None,
        next_sync_token=str(next_sync) if isinstance(next_sync, str) else None,
    )


def _record_from_google_event(item: dict[str, Any]) -> GoogleCalendarEventRecord:
    """Build a :class:`GoogleCalendarEventRecord` from one Google event JSON."""
    attendees = _attendee_emails(item.get("attendees", []))
    organizer = _organizer_email(item.get("organizer", {}))
    start = _datetime_or_date(item.get("start", {}))
    end = _datetime_or_date(item.get("end", {}))
    recurrence = _recurrence_rules(item.get("recurrence", []))
    status = str(item.get("status", "") or "")
    summary = str(item.get("summary", "") or "")
    description = str(item.get("description", "") or "")
    location = str(item.get("location", "") or "")
    updated = str(item.get("updated", "") or "")
    html_link = str(item.get("htmlLink", "") or "")

    return GoogleCalendarEventRecord(
        event_id=str(item.get("id", "")),
        summary=summary,
        description=description,
        start_iso=start,
        end_iso=end,
        location=location,
        attendees=attendees,
        organizer_email=organizer,
        updated_iso=updated,
        recurrence=recurrence,
        status=status,
        html_link=html_link,
        raw_payload=str(item),
    )


def _attendee_emails(raw: list[dict[str, Any]] | Any) -> tuple[str, ...]:
    """Pull attendee email addresses out of the Google ``attendees`` array."""
    if not isinstance(raw, list):
        return ()
    out: list[str] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        addr = entry.get("email")
        if isinstance(addr, str) and addr:
            out.append(addr)
    return tuple(out)


def _organizer_email(raw: dict[str, Any]) -> str:
    """Pull the organizer email out of the Google ``organizer`` object."""
    if not isinstance(raw, dict):
        return ""
    addr = raw.get("email", "")
    return str(addr or "")


def _datetime_or_date(raw: dict[str, Any]) -> str:
    """Pull the ISO timestamp from a Google ``{dateTime, date}`` object.

    All-day events carry ``date`` (YYYY-MM-DD); timed events carry
    ``dateTime`` (RFC 3339). Return whichever is set, preferring
    ``dateTime``.
    """
    if not isinstance(raw, dict):
        return ""
    dt = raw.get("dateTime")
    if isinstance(dt, str) and dt:
        return dt
    d = raw.get("date")
    if isinstance(d, str) and d:
        return d
    return ""


def _recurrence_rules(raw: list[Any] | Any) -> tuple[str, ...]:
    """Pull RRULE / EXRULE / RDATE / EXDATE strings out of Google's recurrence array."""
    if not isinstance(raw, list):
        return ()
    return tuple(str(r) for r in raw if isinstance(r, str))


def iter_pages_initial(
    client: GoogleCalendarClient, time_min_iso: str, first_page: GoogleCalendarEventsPage
) -> Iterator[GoogleCalendarEventsPage]:
    """Drain pages of the initial window walk until ``next_sync_token`` arrives.

    Helper that exposes the events.list pageToken walk as an iterator.
    The connector uses this to assemble the full set of events seen in
    the initial sync tick. The final yielded page carries the
    ``next_sync_token`` to persist as the next cursor.
    """
    yield first_page
    page = first_page
    while page.next_page_token is not None and page.next_sync_token is None:
        page = client.fetch_next_page_initial(time_min_iso, page.next_page_token)
        yield page


def iter_pages_delta(
    client: GoogleCalendarClient, sync_token: str, first_page: GoogleCalendarEventsPage
) -> Iterator[GoogleCalendarEventsPage]:
    """Drain pages of an incremental delta walk until ``next_sync_token`` arrives.

    Mirrors :func:`iter_pages_initial` for the delta path. The final
    yielded page carries the new ``next_sync_token`` for the next tick.
    """
    yield first_page
    page = first_page
    while page.next_page_token is not None and page.next_sync_token is None:
        page = client.fetch_next_page_delta(sync_token, page.next_page_token)
        yield page
