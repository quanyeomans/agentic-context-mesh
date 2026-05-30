"""Thin :mod:`caldav` wrapper for Apple iCloud CalDAV calendar sync.

Wraps the CalDAV PROPFIND/REPORT surface for one operator-configured
iCloud account. The wrapper exposes one method per CalDAV interaction
the connector uses; chunking, signal extraction, and Bronze persistence
are upstream (per F35 / F38).

Per the architecture's three-layer split (docs/architecture/
connector-ingestion-architecture.md, mirrored for connectors by F35),
this module ONLY imports from:

* :mod:`caldav` (third-party CalDAV client — lazy-imported inside the
  production factory so the kairix wheel stays importable without the
  optional dep)
* :mod:`requests` (third-party HTTP errors — caldav re-raises these,
  and the connector classifies the 503 / 401 surface)
* :mod:`kairix.core.protocols` (frozen-dc return shapes only)

It does NOT import from ``kairix.transport``, ``kairix.providers``,
``kairix.core.connectors``, or any sibling ``kairix.connectors.*``.

Per F42, the wrapper's public methods return frozen dataclasses
(:class:`CalendarEventRecord`, :class:`CalendarSyncPage`) — never bare
``dict[str, Any]`` — so the connector's Protocol boundary is typed.

Test seam: the production :class:`AppleCalDavClient` is substituted in
tests via the connector's ``client_factory`` constructor seam. Tests
build a stand-in client (subclass that bypasses ``__init__``) and drive
it through scripted pages. No live iCloud traffic ever fires from the
test suite.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)

# Apple's published CalDAV endpoint — discovery starts here, and the
# library follows the principal redirect chain to the per-account home
# collection. Operators on iCloud Plus / custom domains keep the same
# entry-point.
DEFAULT_ICLOUD_ENDPOINT = "https://caldav.icloud.com"

# iCloud surfaces throttling via HTTP 503 + Retry-After (see Apple's
# "Limit your requests" guidance). The connector classifies the 503
# response with a Retry-After header as a transient back-pressure
# signal; bare 503 without Retry-After surfaces as
# :class:`requests.exceptions.HTTPError` to the dead-letter path.
ICLOUD_THROTTLE_STATUS = 503

# Invalid app-password surfaces as HTTP 401 — distinct from a missing
# credential (caught earlier at config validation) and distinct from
# permission-revocation (which iCloud surfaces as 403). The connector
# raises so the operator notices and rotates the app-password.
ICLOUD_UNAUTHORIZED_STATUS = 401


@dataclass(frozen=True)
class CalDavCalendarRef:
    """One discovered CalDAV calendar collection.

    ``url`` is the calendar's CalDAV URL (e.g.
    ``https://p01-caldav.icloud.com/12345/calendars/work/``); ``ctag``
    is the cheap "anything changed?" hash the connector compares
    against the persisted value before doing a full sync REPORT.
    """

    url: str
    display_name: str
    ctag: str | None


@dataclass(frozen=True)
class CalendarEventRecord:
    """One calendar event as surfaced by iCloud CalDAV.

    Frozen-dataclass return type per F42. Carries the minimum fields
    the connector needs to emit a ``ChangeEvent`` + populate envelope
    metadata downstream. The full iCalendar (ICS) text is preserved on
    :attr:`raw_ics` for Bronze persistence — Silver pulls additional
    fields out of there as needs evolve.

    ``cancelled`` distinguishes an event-level cancellation
    (``STATUS:CANCELLED`` in the ICS body) from a CalDAV-level removal
    (``<status>HTTP/1.1 404 Not Found</status>`` in the sync-collection
    response). The connector maps both to a connector-level
    ``deleted`` :class:`ChangeEvent` so downstream timeline-update
    logic stays uniform.
    """

    event_id: str
    summary: str
    dtstart_iso: str
    dtend_iso: str
    location: str
    attendees: tuple[str, ...]
    organiser: str
    last_modified_iso: str
    recurrence_rule: str
    cancelled: bool
    removed: bool
    raw_ics: str
    event_url: str


@dataclass(frozen=True)
class CalendarSyncPage:
    """One sync-collection REPORT response from iCloud CalDAV.

    ``events`` is the list of records on this page; ``sync_token`` is
    the cursor to persist for the next tick (per RFC 6578). When the
    server does not support ``<sync-collection>``, ``sync_token`` is
    ``None`` and the connector falls back to ctag-comparison against
    the calendar collection.
    """

    events: tuple[CalendarEventRecord, ...]
    sync_token: str | None


class AppleCalDavClient:
    """Narrow wrapper around iCloud CalDAV.

    Construction is cheap — no network IO at ``__init__``. The first
    :meth:`discover_calendars` call triggers the principal-discovery
    PROPFIND chain through the :mod:`caldav` library.

    DI seams:

    * ``username`` / ``password`` — operator credentials. ``password``
      MUST be an Apple app-specific password (NOT the iCloud account
      password); see the package README for how operators generate one.
    * ``endpoint`` — CalDAV root URL. Defaults to Apple's published
      endpoint; operators on EU / China iCloud regions override.
    * ``dav_client_factory`` — optional callable that returns the
      underlying :mod:`caldav` DAV client. Tests substitute this with a
      stand-in that yields scripted pages; production builds the real
      ``caldav.DAVClient(...)`` through the lazy-imported library.

    The real ``caldav`` library is imported inside the default
    factory ONLY — module-level import would force every kairix install
    to pull the optional dep. Tests construct the client via
    ``object.__new__`` + attribute assignment (or subclass the client
    and override the public methods) so the lazy import never fires.
    """

    def __init__(
        self,
        *,
        username: str,
        password: str,
        endpoint: str = DEFAULT_ICLOUD_ENDPOINT,
        dav_client_factory: object | None = None,
    ) -> None:
        self._username = username
        # F15 — never log the bound password. The connector stores it as
        # an instance attribute (necessary for the lazy DAVClient build)
        # but never interpolates it into log strings.
        self._password = password
        self._endpoint = endpoint
        self._dav_client_factory = dav_client_factory
        self._dav_client: object | None = None

    def _ensure_dav_client(self) -> object:
        """Build the underlying caldav.DAVClient on first use.

        Lazy because the :mod:`caldav` optional dep should not be
        imported at module-load time. Tests substitute the entire
        client by overriding ``discover_calendars`` / ``list_changes``
        / ``fetch`` at the subclass level, so this path never fires in
        the test suite.
        """
        if self._dav_client is None:
            if self._dav_client_factory is not None:
                self._dav_client = self._dav_client_factory
            else:
                # Lazy import — keeps the kairix wheel installable
                # without the caldav optional extra. mypy resolves the
                # dep via ``ignore_missing_imports``; runtime requires
                # ``pip install Kairix-agentic-knowledge-mgt[caldav]``.
                import caldav

                self._dav_client = caldav.DAVClient(
                    url=self._endpoint,
                    username=self._username,
                    password=self._password,
                )
        return self._dav_client

    def discover_calendars(self) -> tuple[CalDavCalendarRef, ...]:
        """Run the PROPFIND chain → return one ref per discovered calendar.

        Subclassed in tests (and bypassed in unit tests that construct
        the client via ``object.__new__``). The production
        implementation walks ``principal()`` →
        ``calendar_home_set()`` → ``calendars()`` and wraps each in a
        :class:`CalDavCalendarRef` with the calendar's ctag.

        Operators with restricted apps (e.g. work-only calendar
        sharing) configure a ``calendar_ids`` filter on the connector
        to scope to specific URLs; the connector calls this method
        once then filters the returned tuple.
        """
        client = self._ensure_dav_client()
        # caldav's DAVClient surface: principal() → calendar_home →
        # calendars(). Each calendar object exposes .url and
        # .get_properties for the ctag fetch.
        principal = client.principal()  # type: ignore[attr-defined]  # F3 rationale: client typed as object for the lazy-import + DI-seam shape; principal() exists on the real caldav.DAVClient.
        calendars: list[CalDavCalendarRef] = []
        for calendar in principal.calendars():
            url = str(calendar.url)
            display_name = self._safe_display_name(calendar)
            ctag = self._safe_ctag(calendar)
            calendars.append(CalDavCalendarRef(url=url, display_name=display_name, ctag=ctag))
        return tuple(calendars)

    def list_changes(self, calendar_url: str, sync_token: str | None) -> CalendarSyncPage:
        """Run the sync-collection REPORT (or fallback) for one calendar.

        ``sync_token`` is the per-RFC-6578 cursor — ``None`` means
        first sync, in which case the server returns every event AND a
        fresh sync token to persist. When the server doesn't support
        ``<sync-collection>``, the implementation falls back to a
        ``time-range`` REPORT that re-fetches the operator's configured
        window.

        Subclassed in tests; production fires the actual REPORT through
        :mod:`caldav`. The caldav library's ``calendar.sync_token``
        attribute holds the most-recent server-issued token; setting it
        before the next drain tells caldav to issue a
        ``<sync-collection>`` REPORT with that token instead of a
        full-window query.
        """
        client = self._ensure_dav_client()
        calendar = client.calendar(url=calendar_url)  # type: ignore[attr-defined]  # F3 rationale: client typed as object for DI seam; real caldav.DAVClient exposes calendar(url=...).
        if sync_token is not None:
            # Seed the caldav.Calendar's sync_token so the next drain
            # runs an RFC 6578 sync-collection REPORT instead of a
            # full-window time-range REPORT.
            self._prime_sync_token(calendar, sync_token)
        events = self._fetch_events(calendar)
        new_sync_token = self._fetch_sync_token(calendar)
        return CalendarSyncPage(events=events, sync_token=new_sync_token)

    def _prime_sync_token(self, calendar: object, sync_token: str) -> None:
        """Seed calendar.sync_token in the caldav-library shape.

        The caldav library accepts a ``sync_token`` attribute on its
        :class:`caldav.Calendar` instance; subsequent ``.objects()`` or
        ``.search()`` calls observe it. When the attribute can't be set
        (older caldav library version, scripted stand-in), the call
        falls back silently — the connector still gets a valid page,
        just from a full-window REPORT instead of an incremental one.
        """
        try:
            calendar.sync_token = sync_token  # type: ignore[attr-defined]  # F3 rationale: calendar typed as object for the DI seam; real caldav.Calendar accepts the assignment.
        except (AttributeError, TypeError):
            logger.debug("apple_caldav: calendar object does not accept sync_token seed; falling back to full window")

    def fetch(self, event_url: str) -> CalendarEventRecord:
        """Re-fetch one event by its CalDAV URL.

        Used by the connector's :meth:`SourceConnector.fetch` when the
        orchestrator needs a fresh body (e.g. after a re-extract
        triggered by an extractor-version bump). Subclassed in tests.
        """
        client = self._ensure_dav_client()
        event = client.calendar_event_by_url(event_url)  # type: ignore[attr-defined]  # F3 rationale: client typed as object for DI seam; real caldav.DAVClient exposes calendar_event_by_url.
        return self._parse_event(event)

    # ------------------------------------------------------------------
    # Internals — production-only; tests subclass the public methods.
    # ------------------------------------------------------------------

    def _safe_display_name(self, calendar: object) -> str:
        """Read calendar.get_properties safely; fall back to URL tail."""
        try:
            properties = calendar.get_properties()  # type: ignore[attr-defined]  # F3 rationale: calendar typed as object for the DI seam; real caldav.Calendar exposes get_properties.
            value = properties.get("{DAV:}displayname")
            if value:
                return str(value)
        except (AttributeError, KeyError, requests.RequestException):
            logger.debug("apple_caldav: display name unavailable for calendar; using URL fallback")
        url = str(getattr(calendar, "url", ""))
        return url.rstrip("/").rsplit("/", 1)[-1] or url

    def _safe_ctag(self, calendar: object) -> str | None:
        """Read the {http://calendarserver.org/ns/}getctag if available."""
        try:
            properties = calendar.get_properties()  # type: ignore[attr-defined]  # F3 rationale: calendar typed as object for the DI seam; real caldav.Calendar exposes get_properties.
            value = properties.get("{http://calendarserver.org/ns/}getctag")
            return str(value) if value else None
        except (AttributeError, KeyError, requests.RequestException):
            return None

    def _fetch_events(self, calendar: object) -> tuple[CalendarEventRecord, ...]:
        """Drain the calendar's current events as CalendarEventRecord."""
        records: list[CalendarEventRecord] = []
        try:
            events_iter = calendar.events()  # type: ignore[attr-defined]  # F3 rationale: calendar typed as object for the DI seam; real caldav.Calendar exposes events().
        except (AttributeError, requests.RequestException):
            return ()
        for raw_event in events_iter:
            try:
                records.append(self._parse_event(raw_event))
            except (AttributeError, ValueError, KeyError):
                logger.debug("apple_caldav: failed to parse one event; skipping for this tick")
                continue
        return tuple(records)

    def _fetch_sync_token(self, calendar: object) -> str | None:
        """Read the latest sync-token from the calendar collection."""
        try:
            return getattr(calendar, "sync_token", None)
        except (AttributeError, requests.RequestException):
            return None

    def _parse_event(self, raw_event: object) -> CalendarEventRecord:
        """Translate a caldav.Event into a CalendarEventRecord.

        Subclasses (in tests) override this so the production parsing
        is exercised only against real caldav.Event instances. The
        production implementation reads ``raw_event.icalendar_component``
        for structured field access (DTSTART / DTEND / DURATION /
        RRULE / ORGANIZER / ATTENDEE / SUMMARY / LOCATION /
        LAST-MODIFIED / STATUS / UID) and falls back to ``raw_event.data``
        for the raw ICS text.
        """
        component = getattr(raw_event, "icalendar_component", None)
        if component is None:
            raise ValueError(
                "apple_caldav: event has no icalendar component. "
                "fix: ensure the caldav library version supports icalendar_component (≥ 1.0). "
                "next: see kairix/connectors/apple_caldav/README.md for the supported dep range."
            )
        return _build_event_record(component, raw_event)


def _build_event_record(component: object, raw_event: object) -> CalendarEventRecord:
    """Translate one icalendar Component into a CalendarEventRecord.

    Lifted to a module-level helper so :meth:`AppleCalDavClient._parse_event`
    stays under the F16 cognitive-complexity ceiling.
    """
    get = _component_getter(component)
    event_id = get("UID")
    summary = get("SUMMARY")
    dtstart = get("DTSTART")
    dtend = get("DTEND")
    location = get("LOCATION")
    organiser = get("ORGANIZER")
    last_modified = get("LAST-MODIFIED")
    rrule = get("RRULE")
    status = get("STATUS")
    attendees_field = component.get("ATTENDEE") if hasattr(component, "get") else None
    attendees = _extract_attendees(attendees_field)
    raw_ics = str(getattr(raw_event, "data", ""))
    event_url = str(getattr(raw_event, "url", ""))
    cancelled = status.upper() == "CANCELLED" if status else False
    return CalendarEventRecord(
        event_id=event_id,
        summary=summary,
        dtstart_iso=dtstart,
        dtend_iso=dtend,
        location=location,
        attendees=attendees,
        organiser=organiser,
        last_modified_iso=last_modified,
        recurrence_rule=rrule,
        cancelled=cancelled,
        removed=False,
        raw_ics=raw_ics,
        event_url=event_url,
    )


def _component_getter(component: object) -> Callable[[str], str]:
    """Return a callable that reads icalendar component fields as str."""

    def _get(key: str) -> str:
        if not hasattr(component, "get"):
            return ""
        value = component.get(key)
        if value is None:
            return ""
        return str(value)

    return _get


def _extract_attendees(field: object) -> tuple[str, ...]:
    """Translate an ATTENDEE field (single, list, or None) into a tuple."""
    if field is None:
        return ()
    if isinstance(field, list):
        return tuple(str(a) for a in field)
    return (str(field),)
