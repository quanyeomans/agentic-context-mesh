"""Thin httpx wrapper around Microsoft Graph for calendar event sync.

Wraps ``/users/<id>/calendar/calendarView`` (date-window filter) and
``/users/<id>/calendar/events/delta`` (delta token incremental sync).
The wrapper is deliberately narrow — it exposes one method per Graph
call the connector uses; chunking, signal extraction, and Bronze
persistence are upstream (per F35 / F38).

Per the architecture's three-layer split (docs/architecture/
provider-plugin-architecture.md, mirrored for connectors by F35), this
module ONLY imports from:

* :mod:`httpx` (third-party transport)
* :mod:`kairix.connectors.m365_calendar.auth` (the local OAuth2
  placeholder — see that module's TODO for the post-KP-2 swap path)

It does NOT import from ``kairix.transport``, ``kairix.providers``,
``kairix.core.connectors``, or any sibling ``kairix.connectors.*``.

Per F42, the wrapper's public methods return frozen dataclasses
(:class:`CalendarEventRecord`) or tuples of them — never bare
``dict[str, Any]`` — so the connector's Protocol boundary is typed.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx

from kairix.connectors.m365_calendar.auth import OAuth2ClientCredsAuth

# Microsoft Graph base URL. The connector targets the v1.0 surface
# (calendarView + events/delta are both GA there). Beta surface is not
# used; if a future capability needs the beta endpoint it gets a new
# dedicated method, not a flag here.
GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"

# Default page size for delta queries. Graph caps server-side at 50 for
# calendarView/delta; matching the cap keeps requests round-trip-
# efficient without tripping the cap-rejection error.
DEFAULT_PAGE_SIZE = 50


@dataclass(frozen=True)
class CalendarEventRecord:
    """One calendar event as surfaced by Graph.

    Frozen-dataclass return type per F42. Carries the minimum fields
    the connector needs to emit a ``ChangeEvent`` + populate entity
    signals (attendees → Person/Org) downstream. The full Graph payload
    is preserved on :attr:`raw_payload` for Bronze persistence — Silver
    will pull additional fields out of there as needs evolve.

    ``cancelled`` distinguishes a Graph-level event cancellation
    (``isCancelled: true`` in the Graph payload) from an OData
    ``@removed`` tombstone — Silver maps both to a connector-level
    ``deleted`` :class:`ChangeEvent` so downstream timeline-update
    logic stays uniform.
    """

    event_id: str
    subject: str
    start_iso: str
    end_iso: str
    location: str
    attendees: tuple[str, ...]
    organiser: str
    last_modified_iso: str
    cancelled: bool
    removed: bool
    raw_payload: str


@dataclass(frozen=True)
class CalendarDeltaPage:
    """One page of a Graph delta query.

    ``events`` is the list of records on this page; ``next_link`` (if
    set) is the absolute URL of the next page; ``delta_link`` (if set)
    is the cursor to persist for the next sync tick. Exactly one of
    the two link fields is set on each Graph response per the OData
    delta-query contract.
    """

    events: tuple[CalendarEventRecord, ...]
    next_link: str | None
    delta_link: str | None


class M365GraphCalendarClient:
    """Narrow wrapper around the Graph calendar sync surface.

    Construction is cheap — no HTTP at __init__. The first
    :meth:`fetch_initial_delta` (or :meth:`fetch_delta_page`) call
    triggers the OAuth2 token exchange through
    :class:`OAuth2ClientCredsAuth`.

    DI seams:

    * ``http_client`` — :class:`httpx.Client` instance. Tests inject a
      pre-configured ``MockTransport`` so no real network I/O fires.
      Default is a fresh :class:`httpx.Client` bound to the OAuth2
      auth flow.
    * ``page_size`` — overrides the calendarView ``$top`` parameter.
    """

    def __init__(
        self,
        user_id: str,
        auth: OAuth2ClientCredsAuth,
        http_client: httpx.Client | None = None,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> None:
        self._user_id = user_id
        self._auth = auth
        self._http = http_client or httpx.Client(auth=auth, timeout=60.0)
        self._page_size = page_size

    def fetch_initial_delta(self, start_iso: str, end_iso: str) -> CalendarDeltaPage:
        """First-time sync — pull a date-window of events with no cursor.

        Returns one page of :class:`CalendarEventRecord` plus a
        ``next_link`` / ``delta_link`` for follow-up. The orchestrator
        drains all pages by calling :meth:`fetch_delta_page` in a loop
        until ``delta_link`` is set.

        ``start_iso`` and ``end_iso`` must be ISO-8601 UTC timestamps.
        The connector picks the window (default: 90 days back, 365
        days forward; see :func:`kairix.connectors.m365_calendar.connector.default_window`).
        """
        path = f"/users/{self._user_id}/calendar/calendarView/delta"
        params: dict[str, Any] = {
            "startDateTime": start_iso,
            "endDateTime": end_iso,
            "$top": self._page_size,
        }
        response = self._http.get(f"{GRAPH_BASE_URL}{path}", params=params)
        response.raise_for_status()
        return _parse_delta_response(response.json())

    def fetch_delta_page(self, link: str) -> CalendarDeltaPage:
        """Follow a Graph-returned ``@odata.nextLink`` / ``@odata.deltaLink``.

        Graph encodes the cursor state into the URL — the orchestrator
        passes the exact link back unchanged. The auth flow re-injects
        the Bearer token on every request, so the same client + auth
        wraps both initial and incremental pages.
        """
        response = self._http.get(link)
        response.raise_for_status()
        return _parse_delta_response(response.json())

    def close(self) -> None:
        """Close the underlying :class:`httpx.Client`."""
        self._http.close()

    def __enter__(self) -> M365GraphCalendarClient:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()


def _parse_delta_response(payload: dict[str, Any]) -> CalendarDeltaPage:
    """Translate a Graph JSON payload to a typed :class:`CalendarDeltaPage`.

    Graph's calendarView/delta payload is an OData envelope with::

        {
          "@odata.context": "...",
          "@odata.nextLink": "..." | absent,
          "@odata.deltaLink": "..." | absent,
          "value": [ { event payload }, ... ]
        }

    Tombstoned events carry ``@removed`` instead of a full event body;
    they're surfaced as :attr:`CalendarEventRecord.removed=True`.
    """
    records: list[CalendarEventRecord] = []
    for item in payload.get("value", []):
        records.append(_record_from_graph_event(item))

    next_link_raw = payload.get("@odata.nextLink")
    delta_link_raw = payload.get("@odata.deltaLink")
    next_link = str(next_link_raw) if isinstance(next_link_raw, str) else None
    delta_link = str(delta_link_raw) if isinstance(delta_link_raw, str) else None
    return CalendarDeltaPage(
        events=tuple(records),
        next_link=next_link,
        delta_link=delta_link,
    )


def _record_from_graph_event(item: dict[str, Any]) -> CalendarEventRecord:
    """Build a :class:`CalendarEventRecord` from one Graph event JSON object."""
    if "@removed" in item:
        return CalendarEventRecord(
            event_id=str(item.get("id", "")),
            subject="",
            start_iso="",
            end_iso="",
            location="",
            attendees=(),
            organiser="",
            last_modified_iso="",
            cancelled=False,
            removed=True,
            raw_payload="",
        )

    attendees = _attendee_emails(item.get("attendees", []))
    organiser = _organiser_email(item.get("organizer", {}))
    location_value = _location_display(item.get("location", {}))
    start = _datetime_iso(item.get("start", {}))
    end = _datetime_iso(item.get("end", {}))
    cancelled = bool(item.get("isCancelled", False))
    subject = str(item.get("subject", "") or "")
    last_modified = str(item.get("lastModifiedDateTime", "") or "")

    return CalendarEventRecord(
        event_id=str(item.get("id", "")),
        subject=subject,
        start_iso=start,
        end_iso=end,
        location=location_value,
        attendees=attendees,
        organiser=organiser,
        last_modified_iso=last_modified,
        cancelled=cancelled,
        removed=False,
        raw_payload=str(item),
    )


def _attendee_emails(raw: list[dict[str, Any]] | Any) -> tuple[str, ...]:
    """Pull attendee email addresses out of the Graph ``attendees`` array."""
    if not isinstance(raw, list):
        return ()
    out: list[str] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        email_obj = entry.get("emailAddress", {})
        if not isinstance(email_obj, dict):
            continue
        addr = email_obj.get("address")
        if isinstance(addr, str) and addr:
            out.append(addr)
    return tuple(out)


def _organiser_email(raw: dict[str, Any]) -> str:
    """Pull the organiser email out of the Graph ``organizer`` object."""
    if not isinstance(raw, dict):
        return ""
    email_obj = raw.get("emailAddress", {})
    if not isinstance(email_obj, dict):
        return ""
    addr = email_obj.get("address", "")
    return str(addr or "")


def _location_display(raw: dict[str, Any]) -> str:
    """Pull the display name out of the Graph ``location`` object."""
    if not isinstance(raw, dict):
        return ""
    return str(raw.get("displayName", "") or "")


def _datetime_iso(raw: dict[str, Any]) -> str:
    """Pull the ISO-8601 timestamp out of a Graph ``{dateTime, timeZone}`` object."""
    if not isinstance(raw, dict):
        return ""
    return str(raw.get("dateTime", "") or "")


def iter_pages(client: M365GraphCalendarClient, first_page: CalendarDeltaPage) -> Iterator[CalendarDeltaPage]:
    """Drain pages starting from ``first_page`` until a ``delta_link`` arrives.

    Helper that exposes the OData ``@odata.nextLink`` walk as a Python
    iterator. The connector uses this to assemble the full set of
    events seen in one sync tick. The final yielded page carries the
    ``delta_link`` to persist as the next cursor.
    """
    yield first_page
    page = first_page
    while page.next_link is not None:
        page = client.fetch_delta_page(page.next_link)
        yield page
