"""``GoogleCalendarConnector`` — SourceConnector for Google Calendar.

Implements :class:`kairix.core.protocols.SourceConnector` for one
Google Calendar (one ``calendar_id``, defaulting to ``primary``).
Change detection rides Google's ``events.list`` ``syncToken`` mechanism:

* First sync — no cursor — pulls events from ``now - 30 days`` until
  Google emits a ``nextSyncToken``. Every event surfaces as a
  ``created`` :class:`ChangeEvent`.
* Subsequent syncs — cursor is the persisted ``nextSyncToken`` —
  pulls only the incremental delta since the last tick. New events
  surface as ``created`` / ``modified`` (based on whether the
  connector has seen the event id before); cancelled events (Google
  ``status="cancelled"``) are skipped per the brief — recurring
  master events keep their RRULE in metadata rather than expanding
  into N per-occurrence documents (per ADR-028).

If the persisted syncToken expires (Google 410 Gone), the connector
catches :class:`SyncTokenExpiredError` and transparently falls back
to a fresh initial sync.

Auth uses an OAuth 2.0 access token configured via the operator's
secret-resolution boundary (KV-backed in production; the connector
itself never touches the token endpoint). Google Workspace OAuth
credentials are tracked under GH #356 — until they land, the
connector ships flag-gated OFF (``topology_v2_google_calendar``).

Per F35, this module only imports from
``kairix.connectors.google_calendar.*`` (same plugin) and
``kairix.core.*`` (the Protocol surface). No reach into other
connectors, no reach into the extractor layer.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from kairix.connectors.google_calendar.client import (
    DEFAULT_PAGE_SIZE,
    GoogleCalendarClient,
    GoogleCalendarEventRecord,
    GoogleCalendarEventsPage,
    SyncTokenExpiredError,
    iter_pages_delta,
    iter_pages_initial,
)
from kairix.core.protocols import (
    ChangeEvent,
    Cursor,
    RawArtefact,
    Sensitivity,
    SourceMetadata,
)

CONNECTOR_NAME = "google_calendar"

# Default initial-sync window: 30 days back. Operators can override via
# ``make_connector`` config key ``window_days_back``. Subsequent syncs
# use the returned ``nextSyncToken`` so the window does not apply again.
DEFAULT_INITIAL_WINDOW_DAYS_BACK = 30

# Source-link URI scheme. Google's ``htmlLink`` field on the event
# itself is the canonical user-visible URL; the connector returns that
# when available, with a deterministic fallback if Google did not set
# it (e.g. for cancelled tombstones).
_CALENDAR_HTML_LINK_FALLBACK = "https://calendar.google.com/calendar/u/0/r/eventedit/{event_id}"

# F52 — single literal so the call-site scan picks it up at exactly one
# place. The dispatcher (``kairix.worker.dispatch_google_calendar_sync``)
# reads the same name verbatim.
TOPOLOGY_V2_GOOGLE_CALENDAR_FLAG = "topology_v2_google_calendar"

# Description chunking — render Google's event envelope into a text
# block for the extractor. Each section is separated by a blank line so
# the extractor's default chunker can break on the section boundary.
_RENDER_TITLE_PREFIX = "Title: "
_RENDER_WHEN_PREFIX = "When: "
_RENDER_WHERE_PREFIX = "Where: "
_RENDER_WHO_PREFIX = "Who: "
_RENDER_DESCRIPTION_PREFIX = "Description:\n"
_RENDER_RECURRENCE_PREFIX = "Recurrence:\n"
_RENDER_LINKED_DOCS_PREFIX = "Linked docs:\n"

# F65 — extract linked-document URLs out of the event description. The
# regex finds bare http(s) URLs; we de-duplicate while preserving the
# original order so the metadata is deterministic across ticks.
_URL_PATTERN = re.compile(r"https?://[^\s<>\"'`)]+", re.IGNORECASE)

# Calendar event MIME — the rendered text block is technically a
# RFC 5545 fragment in spirit, so the brief asks us to label it
# ``text/calendar``. Downstream the passthrough extractor handles it
# the same as any plain-text body.
GOOGLE_CALENDAR_MIME = "text/calendar"


@dataclass(frozen=True)
class GoogleCalendarConfig:
    """Resolved configuration for a :class:`GoogleCalendarConnector`.

    Built by :func:`make_connector` from the operator's config block;
    construction-time validation lives in the factory so the connector
    itself can assume well-formed inputs.

    ``access_token`` is the OAuth 2.0 bearer the connector attaches to
    every Google Calendar API call. Per F15, the field name carries
    the ``token`` suffix so the secret-logging gate flags any
    plaintext interpolation outside the boundary modules. Production
    callers resolve the token via the operator's KV-backed secret
    surface (tracked GH #356).

    ``calendar_id`` defaults to ``"primary"`` — the operator's primary
    calendar. Operators can override to a specific calendar id
    (e.g. ``team-calendar@group.calendar.google.com``).
    """

    access_token: str
    calendar_id: str = "primary"
    sensitivity: Sensitivity = "internal"
    window_days_back: int = DEFAULT_INITIAL_WINDOW_DAYS_BACK
    page_size: int = DEFAULT_PAGE_SIZE


@dataclass
class _SyncBatch:
    """Mutable accumulator for one ``list_changes`` call's results."""

    events: list[ChangeEvent] = field(default_factory=list)
    next_sync_token: str | None = None


# Type aliases for the DI seams. ``ClientFactory`` builds the Google
# client from the connector's config; the default uses the production
# httpx + Bearer auth path. Tests inject a factory that returns a
# stand-in wired against an httpx.MockTransport.
ClientFactory = Callable[[GoogleCalendarConfig], GoogleCalendarClient]


def _default_client_factory(config: GoogleCalendarConfig) -> GoogleCalendarClient:
    """Production client factory — builds the httpx + Bearer auth client.

    The access token is attached via httpx's ``headers`` so every
    request carries ``Authorization: Bearer <token>``. Per F15 the
    token name lives only inside this boundary helper; never logged.
    """
    headers = {"Authorization": f"Bearer {config.access_token}"}
    http = httpx.Client(headers=headers, timeout=60.0)
    return GoogleCalendarClient(
        http_client=http,
        calendar_id=config.calendar_id,
        page_size=config.page_size,
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


class GoogleCalendarConnector:
    """SourceConnector for one Google Calendar.

    Construction is cheap (no I/O, no token exchange). The first
    :meth:`list_changes` call triggers the first events.list request.

    DI seams:

    * ``client_factory`` — builds the underlying
      :class:`GoogleCalendarClient`. Tests pass a factory that returns
      a stand-in client; production uses the Bearer + httpx path.
    * ``clock`` — returns the current UTC datetime. Tests substitute a
      :class:`tests.fakes.FakeClock`-like callable so the initial
      ``timeMin`` window is deterministic. F6-clean: the default is a
      real callable.
    """

    name: str = CONNECTOR_NAME
    # F66 — per-tick item budget. Calendar events are small structured
    # envelopes; 500 events / tick is a comfortable cap for typical
    # tenants.
    per_tick_max_items: int = 500
    # F66-watermark-exempt: calendar events are small structured envelopes; no large disk writes.
    disk_watermark_min_free_bytes: int | None = None

    def __init__(
        self,
        config: GoogleCalendarConfig,
        *,
        client_factory: ClientFactory = _default_client_factory,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._config = config
        self._client_factory = client_factory
        self._clock = clock
        self._client: GoogleCalendarClient | None = None
        # Track event ids the connector has emitted as ``created`` so a
        # subsequent delta page tagged with the same id is reported as
        # ``modified`` (Google's events.list with syncToken yields the
        # current state — it doesn't distinguish create vs update).
        self._known_ids: set[str] = set()
        # Cache of the most recent ``nextSyncToken`` — kept on the
        # connector so :meth:`list_changes` callers without a cursor
        # (e.g. tests, cold-start before persistence) still resume from
        # the last known token within one process lifetime.
        self._last_sync_token: str | None = None
        # ADR-021 (Wave E.5): cache per-event envelope metadata so
        # ``metadata_for`` can return organizer + start + attendees
        # without re-hitting Google for an item we already saw on the
        # current tick. Keyed by event_id; populated during ``_drain``.
        self._event_metadata_cache: dict[str, GoogleCalendarEventRecord] = {}
        # Per-process cache of the rendered text body so ``fetch`` can
        # return the same bytes that drove the change-event without
        # re-hitting Google.
        self._event_body_cache: dict[str, bytes] = {}

    # ------------------------------------------------------------------
    # SourceConnector Protocol surface
    # ------------------------------------------------------------------

    def list_changes(self, cursor: Cursor | None) -> Iterator[ChangeEvent]:
        """Stream Google-observed calendar changes since ``cursor``.

        ``cursor`` is the ``nextSyncToken`` from the prior tick.
        ``None`` means first sync — pull the configured time-window.

        On 410 Gone (syncToken too old) the method transparently
        retries with a fresh initial sync, per Google's docs.
        """
        client = self._ensure_client()
        batch = self._drain(client, cursor)
        self._last_sync_token = batch.next_sync_token
        return iter(batch.events)

    def fetch(self, item_id: str) -> RawArtefact:
        """Return the rendered text/calendar body for ``item_id``.

        The events.list drain already cached the structured event;
        :meth:`fetch` renders the cached record into a human-readable
        text block (title / when / where / who / description /
        recurrence / linked-docs) so the extractor chain can chunk it.
        If the orchestrator asks for an id the connector hasn't seen
        this process, the connector raises — there's no point silently
        re-querying.
        """
        record = self._event_metadata_cache.get(item_id)
        if record is None:
            raise ValueError(
                f"google_calendar: no cached event for id {item_id!r}. "
                "fix: drive fetch only against item_ids emitted by list_changes in this process. "
                "next: see docs/architecture/connector-ingestion-architecture.md §10."
            )
        body = self._event_body_cache.get(item_id) or _render_event_body(record).encode("utf-8")
        return RawArtefact(
            raw=body,
            mime=GOOGLE_CALENDAR_MIME,
            fetched_at=_iso(self._clock()),
        )

    def source_link(self, item_id: str) -> str:
        """Return the Google Calendar event link.

        Preferred: the ``htmlLink`` Google returned on the event
        envelope (canonical user-visible URL). Fallback: a
        deterministic ``calendar.google.com`` URL keyed by the event
        id, used when the event was never cached this process (e.g.
        operator queried an id directly).
        """
        record = self._event_metadata_cache.get(item_id)
        if record is not None and record.html_link:
            return record.html_link
        return _CALENDAR_HTML_LINK_FALLBACK.format(event_id=item_id)

    def sensitivity_for(self, _item_id: str) -> Sensitivity:
        """Return the connector's configured sensitivity tier.

        v1 has no per-item overrides. Operators routing personal
        calendars override via ``sensitivity="personal"`` on the
        config block; the work-vs-personal distinction is per-calendar
        not per-event so per-id overrides would be misleading.
        """
        return self._config.sensitivity

    def next_cursor(self) -> str | None:
        """Return the ``nextSyncToken`` to persist after the last drain.

        Google's incremental sync uses opaque tokens; persisting a
        per-event ``updated`` timestamp would force a full window
        rescan every tick. Returns ``None`` until the first successful
        :meth:`list_changes` call has set a token.
        """
        return self._last_sync_token

    def metadata_for(self, item_id: str) -> SourceMetadata:
        """Return cached Google event envelope metadata for ``item_id``.

        ADR-021 / F65: surfaces organizer email as ``author`` /
        ``author_email``, ``updated`` as ``modified_at``, ``start`` as
        ``created_at`` (the meeting's own time, not the row-creation
        time), attendees as ``tags``, and a structured ``properties``
        bag carrying ``start`` / ``end`` / ``duration_minutes`` /
        ``location`` / ``recurrence_rule`` / ``calendar_id`` /
        ``linked_docs`` so downstream retrieval can filter on them.
        Cache miss collapses to an empty :class:`SourceMetadata` so
        an unseen id never crashes the pipeline.
        """
        record = self._event_metadata_cache.get(item_id)
        if record is None:
            return SourceMetadata()

        organizer = record.organizer_email.strip() if record.organizer_email else ""
        author = organizer or None
        author_email = organizer if "@" in organizer else None

        properties: dict[str, str] = {"calendar_id": self._config.calendar_id}
        if record.start_iso:
            properties["start"] = record.start_iso
        if record.end_iso:
            properties["end"] = record.end_iso
        duration = _duration_minutes(record.start_iso, record.end_iso)
        if duration is not None:
            properties["duration_minutes"] = str(duration)
        if record.location:
            properties["location"] = record.location
        if record.recurrence:
            properties["recurrence_rule"] = "\n".join(record.recurrence)

        linked = _extract_linked_docs(record.description)
        if linked:
            properties["linked_docs"] = "\n".join(linked)

        return SourceMetadata(
            modified_at=record.updated_iso or None,
            created_at=record.start_iso or None,
            author=author,
            author_email=author_email,
            tags=record.attendees,
            properties=properties,
        )

    # ------------------------------------------------------------------
    # Cursor + cache accessors (used by the orchestration layer)
    # ------------------------------------------------------------------

    @property
    def last_sync_token(self) -> str | None:
        """The ``nextSyncToken`` to persist as the next cursor.

        ``None`` until the first successful :meth:`list_changes` call.
        After draining, the orchestrator reads this and writes it to
        the ``connector_cursors`` row keyed by the connector's name
        and the calendar id.
        """
        return self._last_sync_token

    def seed_known_ids(self, ids: Iterable[str]) -> None:
        """Pre-populate the known-id set used to distinguish created vs modified.

        Google's events.list with syncToken yields the current state
        of every changed event — it does not tell the caller whether
        a given id is new or already seen. The connector tracks which
        ids it has emitted as ``created`` so the next delta page
        surfaces the same id as ``modified``. Across process restarts
        the orchestrator restores that state by calling
        :meth:`seed_known_ids` with the ids already persisted in the
        documents table.
        """
        self._known_ids.update(ids)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the underlying Google client.

        Idempotent — safe to call multiple times.
        """
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> GoogleCalendarConnector:
        self._ensure_client()
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _ensure_client(self) -> GoogleCalendarClient:
        if self._client is None:
            self._client = self._client_factory(self._config)
        return self._client

    def _drain(self, client: GoogleCalendarClient, cursor: Cursor | None) -> _SyncBatch:
        """Walk Google events.list pages and convert to :class:`ChangeEvent`.

        On 410 Gone from a delta call (syncToken expired) transparently
        falls back to a fresh initial sync per Google's docs.
        """
        batch = _SyncBatch()
        try:
            first_page, pages = self._fetch_first_page(client, cursor)
        except SyncTokenExpiredError:
            # 410 Gone — discard cursor, run a fresh initial sync.
            first_page, pages = self._fetch_first_page(client, None)
        for page in pages(first_page):
            self._absorb_page(page, batch)
        return batch

    def _fetch_first_page(
        self, client: GoogleCalendarClient, cursor: Cursor | None
    ) -> tuple[GoogleCalendarEventsPage, Callable[[GoogleCalendarEventsPage], Iterator[GoogleCalendarEventsPage]]]:
        """First page of a sync tick — initial window or delta follow-up.

        Returns the first page AND a closure that drains subsequent
        pages with the matching query (initial vs delta) so the caller
        does not have to re-branch on cursor.
        """
        if cursor is not None:
            first = client.fetch_delta_events(cursor)
            return first, lambda p: iter_pages_delta(client, cursor, p)
        if self._last_sync_token is not None:
            token = self._last_sync_token
            first = client.fetch_delta_events(token)
            return first, lambda p: iter_pages_delta(client, token, p)
        time_min = _iso(self._clock() - timedelta(days=self._config.window_days_back))
        first = client.fetch_initial_events(time_min)
        return first, lambda p: iter_pages_initial(client, time_min, p)

    def _absorb_page(self, page: GoogleCalendarEventsPage, batch: _SyncBatch) -> None:
        """Translate one events page into ChangeEvents on ``batch``.

        Updates ``batch.next_sync_token`` when the page carries one.
        Cancelled events are skipped per the brief (ADR-028 — no
        per-occurrence expansion, no tombstone-as-deleted for recurring
        masters).
        """
        for record in page.events:
            event = self._record_to_change_event(record)
            if event is None:
                continue
            batch.events.append(event)
            self._event_metadata_cache[record.event_id] = record
            self._event_body_cache[record.event_id] = _render_event_body(record).encode("utf-8")
        if page.next_sync_token is not None:
            batch.next_sync_token = page.next_sync_token

    def _record_to_change_event(self, record: GoogleCalendarEventRecord) -> ChangeEvent | None:
        """Translate one record to a :class:`ChangeEvent`.

        Returns ``None`` for cancelled events (Google
        ``status="cancelled"``) — per the brief these are skipped, not
        emitted as ``deleted``. Recurring masters with RRULE flow
        through as a single ``created`` / ``modified`` event with the
        RRULE captured in :meth:`metadata_for`.
        """
        if record.status == "cancelled":
            return None
        if record.event_id in self._known_ids:
            op: Any = "modified"
        else:
            self._known_ids.add(record.event_id)
            op = "created"
        return ChangeEvent(
            op=op,
            item_id=record.event_id,
            modified_at=record.updated_iso or _iso(self._clock()),
            metadata={
                "summary": record.summary,
                "start": record.start_iso,
                "end": record.end_iso,
                "location": record.location,
                "attendees": record.attendees,
                "organizer": record.organizer_email,
            },
        )


# ---------------------------------------------------------------------------
# Rendering + metadata helpers (module-level for testability)
# ---------------------------------------------------------------------------


def _render_event_body(record: GoogleCalendarEventRecord) -> str:
    """Render a calendar event into a plain-text block.

    Each section is separated by a blank line so a chunker that breaks
    on double-newline produces logical sub-sections. The order is:
    title, when, where, who, description, recurrence, linked docs.
    """
    parts: list[str] = []
    if record.summary:
        parts.append(f"{_RENDER_TITLE_PREFIX}{record.summary}")
    when = _format_when(record.start_iso, record.end_iso)
    if when:
        parts.append(f"{_RENDER_WHEN_PREFIX}{when}")
    if record.location:
        parts.append(f"{_RENDER_WHERE_PREFIX}{record.location}")
    if record.attendees:
        parts.append(f"{_RENDER_WHO_PREFIX}{', '.join(record.attendees)}")
    if record.description:
        parts.append(f"{_RENDER_DESCRIPTION_PREFIX}{record.description}")
    if record.recurrence:
        parts.append(f"{_RENDER_RECURRENCE_PREFIX}{chr(10).join(record.recurrence)}")
    linked = _extract_linked_docs(record.description)
    if linked:
        parts.append(f"{_RENDER_LINKED_DOCS_PREFIX}{chr(10).join(linked)}")
    return "\n\n".join(parts)


def _format_when(start_iso: str, end_iso: str) -> str:
    if start_iso and end_iso:
        return f"{start_iso} -> {end_iso}"
    return start_iso or end_iso or ""


def _extract_linked_docs(description: str) -> tuple[str, ...]:
    """Pull URLs out of an event description, de-duplicated in order.

    F65 wants the regex extraction deterministic so downstream filters
    can pin "events with a linked Google Doc" predicates. We keep the
    first occurrence of each URL and drop duplicates that appear later
    in the description.
    """
    if not description:
        return ()
    seen: set[str] = set()
    out: list[str] = []
    for match in _URL_PATTERN.finditer(description):
        url = match.group(0)
        # Strip trailing punctuation that often follows a URL in prose.
        url = url.rstrip(".,;:!?)")
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
    return tuple(out)


def _duration_minutes(start_iso: str, end_iso: str) -> int | None:
    """Compute ``end - start`` in whole minutes, or ``None`` on parse failure.

    Google sometimes returns ``YYYY-MM-DD`` for all-day events; we
    skip duration in that case (the caller surfaces start/end
    separately).
    """
    if not start_iso or not end_iso:
        return None
    start = _parse_iso(start_iso)
    end = _parse_iso(end_iso)
    if start is None or end is None:
        return None
    delta = end - start
    return int(delta.total_seconds() // 60)


def _parse_iso(value: str) -> datetime | None:
    """Best-effort ISO-8601 parse; returns ``None`` on failure."""
    try:
        # Python's fromisoformat accepts the Z suffix from 3.11+.
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def make_connector(config: Mapping[str, Any]) -> GoogleCalendarConnector:
    """Construct a :class:`GoogleCalendarConnector` from a config mapping.

    Expected keys:

    * ``access_token`` (required) — OAuth 2.0 access token resolved via
      the operator's secret-resolution boundary. Tracked GH #356.
    * ``calendar_id`` (optional) — Google calendar id; defaults to
      ``"primary"``.
    * ``sensitivity`` (optional) — one of the F39 sensitivity literals;
      defaults to ``"internal"``. Use ``"personal"`` for personal
      calendars per ADR-005.
    * ``window_days_back`` (optional) — initial-sync window in days;
      defaults to 30.
    * ``page_size`` (optional) — events.list maxResults; defaults to
      250.

    Registered via ``[project.entry-points."kairix.connectors"]`` in
    kairix's ``pyproject.toml`` so the orchestration layer resolves
    ``google_calendar`` to this factory by name.
    """
    if not config.get("access_token"):
        raise ValueError(
            "google_calendar: config is missing required key 'access_token'. "
            "fix: declare access_token under the google_calendar connector "
            "block in kairix.config.yaml; secrets resolve via the operator's "
            "secret-resolution path, not env vars. "
            "next: see kairix/connectors/google_calendar/README.md and GH #356 "
            "for the credential provisioning runbook."
        )

    resolved = GoogleCalendarConfig(
        access_token=str(config["access_token"]),
        calendar_id=str(config.get("calendar_id", "primary")),
        sensitivity=config.get("sensitivity", "internal"),
        window_days_back=int(config.get("window_days_back", DEFAULT_INITIAL_WINDOW_DAYS_BACK)),
        page_size=int(config.get("page_size", DEFAULT_PAGE_SIZE)),
    )
    return GoogleCalendarConnector(resolved)
