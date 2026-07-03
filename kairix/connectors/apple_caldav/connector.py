"""``AppleCalDavConnector`` — SourceConnector for iCloud CalDAV calendars.

Implements :class:`kairix.core.protocols.SourceConnector` plus the
capability shims (PollConnector, CheckpointedConnector, HierarchyConnector)
for one iCloud account's calendars. Change detection rides the CalDAV
``<sync-collection>`` REPORT token (RFC 6578), with a ctag-comparison
fallback when the server doesn't support sync tokens:

* First sync — no cursor — runs ``<sync-collection>`` with no token
  against every discovered (or operator-filtered) calendar URL.
  Every event surfaces as a ``created`` :class:`ChangeEvent`.
* Subsequent syncs — cursor is the persisted per-calendar sync token —
  drains only the incremental changes since that token. New events
  surface as ``created`` / ``modified`` (based on whether the
  orchestrator has seen the event id before); cancelled events with
  ``STATUS:CANCELLED`` AND removed events surface as ``deleted``.

Auth: HTTP Basic with the operator's iCloud username + an
Apple-issued app-specific password. The connector NEVER accepts the
operator's primary iCloud password — app-specific passwords are the
documented Apple surface for CalDAV. See the package README for the
operator instructions.

Per F35, this module only imports from
``kairix.connectors.apple_caldav.*`` (same plugin) and ``kairix.core.*``
(the Protocol surface). No reach into other connectors, no reach into
the extractor layer.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from kairix.connectors.apple_caldav.client import (
    DEFAULT_ICLOUD_ENDPOINT,
    AppleCalDavClient,
    CalDavCalendarRef,
    CalendarEventRecord,
    CalendarSyncPage,
)
from kairix.core.protocols import (
    ChangeEvent,
    Container,
    Cursor,
    HierarchyNode,
    RawArtefact,
    Sensitivity,
    SourceMetadata,
)
from kairix.secrets import Scope, SecretsResolver

CONNECTOR_NAME = "apple_caldav"

# Canonical secret-area name for this connector. Module-level constant
# so the hierarchy-root id, the secret-identity tuples, and any future
# canonical-name references all key off one literal — F17 (no string
# literal ≥10 chars duplicated ≥3 times).
_AREA_APPLE_CALDAV = "apple-caldav"

# Canonical secret identity tuples for the apple_caldav connector. The
# loader resolves both via the canonical ``KAIRIX_CONNECTOR_APPLE_CALDAV_*``
# env vars or a KV-mount file at
# ``/run/kairix/secrets/kairix-connector-apple-caldav-<leaf>``.
_SECRET_SCOPE_USERNAME: tuple[Scope, str, str | None, str] = ("connector", _AREA_APPLE_CALDAV, None, "username")
_SECRET_SCOPE_ACCESS: tuple[Scope, str, str | None, str] = ("connector", _AREA_APPLE_CALDAV, None, "access")

# Hierarchy root node id for the calendar tree. Each discovered (or
# operator-pinned) calendar becomes a child FOLDER node under this root.
_HIERARCHY_ROOT_ID = _AREA_APPLE_CALDAV

# Source-link scheme — the canonical iCloud Calendar deeplink. Apple's
# Calendar.app accepts the CalDAV URL directly, and the kairix UI
# wraps the link in a "open in Calendar" affordance.
_CALDAV_SOURCE_LINK_SCHEME = "caldav://"


@dataclass(frozen=True)
class AppleCalDavConfig:
    """Resolved configuration for an :class:`AppleCalDavConnector`.

    Built by :func:`make_connector` from the operator's config block;
    construction-time validation lives in the factory so the connector
    itself can assume well-formed inputs.

    Per F15, the ``password`` field name carries the secret suffix so
    the secret-logging gate flags any plaintext interpolation outside
    the boundary modules.

    ``calendar_ids`` (optional) scopes which calendar URLs the
    connector syncs. Empty = discover everything iCloud surfaces;
    populated = only the listed URLs (operator pins specific calendars
    by URL after a one-shot ``kairix discover apple-caldav`` run).
    """

    username: str
    password: str
    endpoint: str = DEFAULT_ICLOUD_ENDPOINT
    sensitivity: Sensitivity = "personal"
    calendar_ids: tuple[str, ...] = ()


@dataclass
class _SyncBatch:
    """Mutable accumulator for one ``list_changes`` call's results."""

    events: list[ChangeEvent] = field(default_factory=list)
    sync_token: str | None = None


# Type aliases for the DI seams. ``ClientFactory`` builds the
# CalDAV client from the connector's config; the default uses the
# production caldav-library path. Tests inject a factory that returns
# a stand-in wired against scripted pages.
ClientFactory = Callable[[AppleCalDavConfig], AppleCalDavClient]


def _default_client_factory(config: AppleCalDavConfig) -> AppleCalDavClient:
    """Production client factory — builds AppleCalDavClient with basic auth."""
    return AppleCalDavClient(
        username=config.username,
        password=config.password,
        endpoint=config.endpoint,
    )


_UTC_OFFSET_SUFFIX = "+00:00"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace(_UTC_OFFSET_SUFFIX, "Z")


def _duration_minutes(start_iso: str, end_iso: str) -> int | None:
    """Compute event duration in minutes from ISO-8601 strings.

    Defensive: iCloud surfaces dt-only events (all-day) without time;
    treat as 24h. Returns ``None`` when either bound is unparseable so
    the metadata stays consistent (missing rather than wrong).
    """
    if not start_iso or not end_iso:
        return None
    try:
        start = datetime.fromisoformat(start_iso.replace("Z", _UTC_OFFSET_SUFFIX))
        end = datetime.fromisoformat(end_iso.replace("Z", _UTC_OFFSET_SUFFIX))
    except (ValueError, TypeError):
        return None
    return int((end - start).total_seconds() // 60)


# F66-watermark-exempt: ICS event envelopes are small (~1-5 KB); no large disk writes
class AppleCalDavConnector:
    """SourceConnector for one iCloud account's CalDAV calendars.

    Construction is cheap (no network IO, no PROPFIND chain). The first
    :meth:`list_changes` call triggers calendar discovery + the first
    ``<sync-collection>`` REPORT.

    DI seams:

    * ``client_factory`` — builds the underlying
      :class:`AppleCalDavClient`. Tests pass a factory that returns a
      stand-in client; production uses the basic-auth + caldav-library
      path.
    * ``clock`` — returns the current UTC datetime. Tests substitute a
      :class:`tests.fakes.FakeClock`-like callable so the modified_at
      defaults stay deterministic. F6-clean: the default is a real callable.
    """

    name: str = CONNECTOR_NAME
    per_tick_max_items: int = 500
    # F66-watermark-exempt: ICS event envelopes are small (~1-5 KB); the
    # connector never writes raw bytes to disk — RawArtefact bytes flow
    # straight to Bronze, then to chunking. No multi-GB attachment risk
    # like the SharePoint / Drive paths.
    disk_watermark_min_free_bytes: int | None = None

    def __init__(
        self,
        config: AppleCalDavConfig,
        *,
        client_factory: ClientFactory = _default_client_factory,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._config = config
        self._client_factory = client_factory
        self._clock = clock
        self._client: AppleCalDavClient | None = None
        # Track event ids the connector has emitted as ``created`` so a
        # subsequent sync page tagged with the same id is reported as
        # ``modified`` (CalDAV's sync surface doesn't distinguish the
        # two — it just yields the current state).
        self._known_ids: set[str] = set()
        # Cache of the most recent per-calendar sync token. Keyed by
        # calendar URL — under the legacy single-cursor path the
        # connector folds them into one composite cursor string.
        self._sync_tokens: dict[str, str | None] = {}
        # ADR-021 — cache per-event envelope metadata so
        # ``metadata_for`` can return organiser + start + RRULE without
        # re-hitting CalDAV for an item we already saw on the current
        # tick. Keyed by event_id; populated during ``_drain``.
        self._event_metadata_cache: dict[str, CalendarEventRecord] = {}
        # Cache of discovered calendar refs (one PROPFIND chain per
        # process). Tests reset by constructing a fresh connector.
        self._calendars: tuple[CalDavCalendarRef, ...] | None = None
        # Cache the raw ICS payload per event id so :meth:`fetch` can
        # answer from memory without re-querying CalDAV.
        self._event_payload_cache: dict[str, bytes] = {}

    # ------------------------------------------------------------------
    # SourceConnector Protocol surface
    # ------------------------------------------------------------------

    def list_changes(self, cursor: Cursor | None) -> Iterator[ChangeEvent]:
        """Stream CalDAV-observed calendar changes since ``cursor``.

        Legacy single-cursor path: ``cursor`` is the composite token
        the connector emitted on the last tick (one sync token per
        calendar, pipe-delimited). ``None`` means first sync — discover
        calendars + drain every event.

        The cursor to persist for the next call is exposed via
        :meth:`next_cursor` after the iterator is drained. The
        per-calendar token map (``self._sync_tokens``) accumulates
        through :meth:`_absorb_page_into_batch` so each calendar's
        latest sync token survives across ticks.
        """
        client = self._ensure_client()
        batch = self._drain_all_calendars(client, cursor)
        return iter(batch.events)

    def fetch(self, item_id: str) -> RawArtefact:
        """Return the raw ICS payload for ``item_id``.

        The CalDAV sync REPORT already brought the full ICS body
        through; rather than re-fetch, the connector caches the most
        recent payload per id during :meth:`list_changes` and returns
        it here. If the orchestrator asks for an id the connector
        hasn't seen this process, raise — there's no point silently
        re-querying when Bronze already has the bytes for that id.
        """
        if item_id not in self._event_payload_cache:
            raise ValueError(
                f"apple_caldav: no cached payload for event id {item_id!r}. "
                "fix: drive fetch only against item_ids emitted by list_changes in this process. "
                "next: see docs/architecture/connector-ingestion-architecture.md §10."
            )
        payload = self._event_payload_cache[item_id]
        return RawArtefact(
            raw=payload,
            mime="text/calendar",
            fetched_at=_iso(self._clock()),
        )

    def source_link(self, item_id: str) -> str:
        """Return a CalDAV-scheme deeplink for ``item_id``.

        When the connector has a cached event URL (populated by
        :meth:`list_changes`), the link is the calendar's CalDAV URL +
        the event id — Calendar.app and most third-party calendar
        clients accept this directly. When the id is unknown the link
        falls back to ``caldav://<id>`` so the operator at least sees
        the identifier.
        """
        record = self._event_metadata_cache.get(item_id)
        if record is not None and record.event_url:
            return record.event_url
        return f"{_CALDAV_SOURCE_LINK_SCHEME}{item_id}"

    def sensitivity_for(self, _item_id: str) -> Sensitivity:
        """Return the connector's configured sensitivity tier.

        Defaults to ``personal`` per the dispatch brief — iCloud
        calendars are operator-personal data. Per-event downgrade
        (e.g. work-only meetings → ``internal``) is a future PR.
        """
        return self._config.sensitivity

    def next_cursor(self) -> str | None:
        """Return the composite sync-token cursor to persist.

        Folds the per-calendar sync tokens into a single pipe-
        delimited string (``<url>=<token>|<url>=<token>``) so the
        legacy single-cursor path can persist them via one
        ``connector_cursors`` row. Wave E's per-container path uses
        :class:`Container.cursor_token` for per-calendar isolation.

        Returns ``None`` when no calendars have produced a token (e.g.
        first cold start before any drain).
        """
        if not self._sync_tokens:
            return None
        parts = [f"{url}={tok or ''}" for url, tok in sorted(self._sync_tokens.items())]
        return "|".join(parts)

    def metadata_for(self, item_id: str) -> SourceMetadata:
        """Return cached CalDAV event envelope metadata for ``item_id``.

        ADR-021: iCloud CalDAV carries organiser + last-modified +
        attendees + location + RRULE + DTSTART / DTEND on the envelope;
        the connector lifts each onto :class:`SourceMetadata` so the
        downstream Silver / search layers can surface them. Cache miss
        collapses to an empty :class:`SourceMetadata` so an unseen id
        never crashes the pipeline.
        """
        record = self._event_metadata_cache.get(item_id)
        if record is None:
            return SourceMetadata()
        author = record.organiser.strip() if record.organiser else None
        author_email = author if author and "@" in author else None
        properties: dict[str, str] = {}
        if record.summary:
            properties["summary"] = record.summary
        if record.dtstart_iso:
            properties["start"] = record.dtstart_iso
        if record.dtend_iso:
            properties["end"] = record.dtend_iso
        if record.location:
            properties["location"] = record.location
        if record.recurrence_rule:
            properties["recurrence_rule"] = record.recurrence_rule
        duration = _duration_minutes(record.dtstart_iso, record.dtend_iso)
        if duration is not None:
            properties["duration_minutes"] = str(duration)
        return SourceMetadata(
            modified_at=record.last_modified_iso or None,
            created_at=record.dtstart_iso or None,
            author=author,
            author_email=author_email,
            tags=record.attendees,
            properties=properties,
        )

    # ------------------------------------------------------------------
    # Topology Wave B / E — capability shims
    # ------------------------------------------------------------------
    # The shims below let the connector satisfy the new capability
    # Protocols (PollConnector, CheckpointedConnector,
    # HierarchyConnector, CredentialsConnector). Production routing
    # through these methods is gated by ``topology_apple_caldav``
    # (default-off).

    def load_from_checkpoint(self, _container: Container, checkpoint: str | None) -> Iterator[ChangeEvent]:
        """CheckpointedConnector shim — delegate to :meth:`list_changes`.

        CalDAV sync tokens are opaque strings; the shim forwards
        ``checkpoint`` directly to :meth:`list_changes` so observable
        behaviour matches the legacy path.
        """
        return self.list_changes(checkpoint)

    def load_credentials(self, credentials: dict[str, Any]) -> dict[str, Any] | None:
        """CredentialsConnector shim — return the input unchanged.

        Basic-auth flow consumes the operator-supplied username +
        app-password as-is; no transformation, no token exchange at
        this surface.
        """
        return credentials

    def iter_containers(self, cc_pair_id: int) -> Iterator[Container]:
        """Yield one :class:`Container` per discovered (or operator-pinned) calendar.

        Topology §4: each Container has its own cursor — the Wave E
        pilot maps each iCloud calendar to its own Container so the
        operator can add or remove individual calendars without
        affecting the cursor state of the others.

        ``access_state`` is always ``ACCESSIBLE`` — basic-auth either
        grants or doesn't grant the configured account at the
        ``client.principal()`` stage; per-calendar permission drift
        surfaces as a request-time error, not at iteration.
        ``cursor_token`` and ``last_synced_at`` start ``None``; the
        framework persists subsequent values (the CalDAV sync token)
        to the ``topology_containers`` table.

        Single-calendar fallback: when the config declares no
        ``calendar_ids`` filter, the connector discovers every
        calendar iCloud surfaces and yields one Container per.
        """
        for ref in self._configured_calendars():
            yield Container(
                cc_pair_id=cc_pair_id,
                container_id=ref.url,
                access_state="ACCESSIBLE",
                cursor_token=None,
                last_synced_at=None,
            )

    def list_changes_for_container(self, container: Container) -> Iterator[ChangeEvent]:
        """Stream calendar events for one Container.

        Reads ``container.cursor_token`` as the per-calendar CalDAV
        sync token (``None`` on first sync) and drains the sync REPORT
        for THAT calendar only. Per-calendar isolation means adding or
        removing one calendar does not affect the cursor state of the
        others.

        ``topology_apple_caldav`` retired post-cutover (task #132);
        the per-calendar path is now the only behaviour.
        """
        return self._list_changes_scoped(container)

    def load_hierarchy(self, cc_pair_id: int) -> Iterator[HierarchyNode]:
        """HierarchyConnector — emit FOLDER nodes parent-before-child.

        Emits a root FOLDER node (``raw_node_id="apple-caldav"``,
        ``raw_parent_id=None``) followed by one child FOLDER node per
        configured calendar, with ``raw_node_id`` set to the calendar
        URL and ``raw_parent_id`` pointing at the root. Parent-before-
        child per F58.

        Per-calendar event grouping (recurring meeting groups,
        category labels) is a Wave-E+1 enhancement — this slice keeps
        the hierarchy at calendar-as-folder granularity to mirror the
        sibling m365_calendar pilot.
        """
        yield HierarchyNode(
            cc_pair_id=cc_pair_id,
            raw_node_id=_HIERARCHY_ROOT_ID,
            raw_parent_id=None,
            display_name="Apple Calendars",
            link=None,
            node_type="FOLDER",
            external_access_json=None,
            sensitivity_hint=None,
        )
        for ref in self._configured_calendars():
            yield HierarchyNode(
                cc_pair_id=cc_pair_id,
                raw_node_id=ref.url,
                raw_parent_id=_HIERARCHY_ROOT_ID,
                display_name=ref.display_name,
                link=None,
                node_type="FOLDER",
                external_access_json=None,
                sensitivity_hint=None,
            )

    # ------------------------------------------------------------------
    # Cursor + cache accessors (used by the orchestration layer)
    # ------------------------------------------------------------------

    def seed_known_ids(self, ids: Iterable[str]) -> None:
        """Pre-populate the known-id set used to distinguish created vs modified.

        CalDAV's sync REPORT surfaces the current state of every event
        — it doesn't tell the caller whether a given id is new or
        already seen. The connector tracks which ids it has emitted as
        ``created`` so the next sync page surfaces the same id as
        ``modified``. Across process restarts the orchestrator restores
        that state by calling :meth:`seed_known_ids` with the ids
        already persisted in the documents table.
        """
        self._known_ids.update(ids)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the underlying CalDAV client. Idempotent."""
        # The caldav library's DAVClient owns no persistent connection
        # (requests-backed pooling is per-call); clearing the reference
        # is sufficient.
        self._client = None

    def __enter__(self) -> AppleCalDavConnector:
        self._ensure_client()
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _ensure_client(self) -> AppleCalDavClient:
        if self._client is None:
            self._client = self._client_factory(self._config)
        return self._client

    def _configured_calendars(self) -> tuple[CalDavCalendarRef, ...]:
        """Return the calendars to sync — discovered then filtered.

        Caches the discovery result for the process lifetime so
        repeated iter_containers / load_hierarchy calls don't re-fire
        the PROPFIND chain.
        """
        if self._calendars is not None:
            return self._calendars
        client = self._ensure_client()
        discovered = client.discover_calendars()
        filter_set = set(self._config.calendar_ids)
        if filter_set:
            filtered = tuple(c for c in discovered if c.url in filter_set)
        else:
            filtered = discovered
        self._calendars = filtered
        return filtered

    def _drain_all_calendars(self, client: AppleCalDavClient, cursor: Cursor | None) -> _SyncBatch:
        """Drain every configured calendar; fold into one batch.

        Legacy single-cursor path: the composite cursor is parsed back
        into per-calendar sync tokens (or ``None`` for first sync); each
        calendar's REPORT runs independently; outputs are folded into
        one events list.
        """
        per_calendar_tokens = _parse_composite_cursor(cursor)
        batch = _SyncBatch()
        for ref in self._configured_calendars():
            token = per_calendar_tokens.get(ref.url)
            page = client.list_changes(ref.url, token)
            self._absorb_page_into_batch(page, batch, ref.url)
        return batch

    def _list_changes_scoped(self, container: Container) -> Iterator[ChangeEvent]:
        """Wave E ON-branch: drain CalDAV REPORT for one container's calendar URL.

        Reads the container's own ``cursor_token`` (the per-calendar
        CalDAV sync token) and runs ``<sync-collection>`` REPORT for
        the container's URL only. Each container's cursor is read
        independently — adding or removing one calendar does not
        disturb another calendar's resume position.
        """
        client = self._ensure_client()
        page = client.list_changes(container.container_id, container.cursor_token)
        batch = _SyncBatch()
        self._absorb_page_into_batch(page, batch, container.container_id)
        return iter(batch.events)

    def _absorb_page_into_batch(self, page: CalendarSyncPage, batch: _SyncBatch, calendar_url: str) -> None:
        """Translate one CalDAV sync page into ChangeEvents on ``batch``."""
        for record in page.events:
            event = self._record_to_change_event(record)
            if event is None:
                continue
            batch.events.append(event)
            if not record.removed:
                self._event_payload_cache[record.event_id] = record.raw_ics.encode("utf-8")
                self._event_metadata_cache[record.event_id] = record
        if page.sync_token is not None:
            self._sync_tokens[calendar_url] = page.sync_token

    def _record_to_change_event(self, record: CalendarEventRecord) -> ChangeEvent | None:
        """Translate one :class:`CalendarEventRecord` to a :class:`ChangeEvent`."""
        modified_at = record.last_modified_iso or _iso(self._clock())
        if record.removed or record.cancelled:
            return ChangeEvent(
                op="deleted",
                item_id=record.event_id,
                modified_at=modified_at,
            )
        if record.event_id in self._known_ids:
            op: Any = "modified"
        else:
            self._known_ids.add(record.event_id)
            op = "created"
        return ChangeEvent(
            op=op,
            item_id=record.event_id,
            modified_at=modified_at,
            metadata={
                "summary": record.summary,
                "start": record.dtstart_iso,
                "end": record.dtend_iso,
                "location": record.location,
                "attendees": record.attendees,
                "organiser": record.organiser,
                "recurrence_rule": record.recurrence_rule,
            },
        )


def _parse_composite_cursor(cursor: Cursor | None) -> dict[str, str | None]:
    """Parse the connector's composite cursor back into per-calendar tokens.

    The composite shape is ``<url>=<token>|<url>=<token>`` (sorted by
    URL on emit). Returns an empty map for None / malformed input so
    first sync / corrupted-cursor cases fall through to no-token
    behaviour.
    """
    if not cursor:
        return {}
    out: dict[str, str | None] = {}
    for entry in cursor.split("|"):
        if "=" not in entry:
            continue
        url, _, token = entry.partition("=")
        if not url:
            continue
        out[url] = token or None
    return out


def make_connector(
    config: Mapping[str, Any],
    *,
    secrets: SecretsResolver | None = None,
) -> AppleCalDavConnector:
    """Construct an :class:`AppleCalDavConnector` from a config mapping.

    Expected keys:

    * ``username`` (optional) — the iCloud Apple ID (e.g.
      ``operator@example.com``). When absent, resolved via ``secrets``
      from the canonical ``connector/apple-caldav/-/username`` identity
      tuple (which the loader's legacy-alias fallback maps to the
      historical ``CONNECTOR_APPLE_CALDAV_USERNAME`` env var).
    * ``password`` (optional) — the Apple-issued app-specific password
      (NOT the iCloud account password). When absent, resolved via
      ``secrets`` from the canonical ``connector/apple-caldav/-/access``
      identity tuple (legacy alias: ``CONNECTOR_APPLE_CALDAV_PASSWORD``).
      See the package README for operator instructions.
    * ``endpoint`` (optional) — CalDAV root URL; defaults to
      ``https://caldav.icloud.com``.
    * ``sensitivity`` (optional) — one of the F39 sensitivity
      literals; defaults to ``"personal"`` per the dispatch brief
      (iCloud calendars are operator-personal data).
    * ``calendar_ids`` (optional) — list/tuple of CalDAV URLs to scope
      the connector to. Empty / unset = discover all calendars.

    Args:
      config: operator-supplied connector config mapping.
      secrets: optional :class:`SecretsResolver` injection seam. When
        ``None`` (production default), a :class:`SecretsLoader` is
        constructed lazily — its env / KV mount / legacy alias chain
        resolves the credentials. Tests inject
        :class:`tests.fakes.FakeSecretsLoader` so no env-var monkey-
        patching is needed (F2-clean).

    Registered via ``[project.entry-points."kairix.connectors"]`` in
    kairix's ``pyproject.toml`` so the orchestration layer resolves
    ``apple_caldav`` to this factory by name.
    """
    username_raw = config.get("username")
    password_raw = config.get("password")

    # Resolve any missing field via the canonical secrets surface; the
    # loader's legacy-alias fallback covers the historical env-var path.
    if not username_raw or not password_raw:
        loader = secrets if secrets is not None else _default_secrets_loader()
        if not username_raw:
            username_raw = loader.require(*_SECRET_SCOPE_USERNAME)
        if not password_raw:
            password_raw = loader.require(*_SECRET_SCOPE_ACCESS)

    resolved = AppleCalDavConfig(
        username=str(username_raw),
        password=str(password_raw),
        endpoint=str(config.get("endpoint", DEFAULT_ICLOUD_ENDPOINT)),
        sensitivity=config.get("sensitivity", "personal"),
        calendar_ids=tuple(str(c) for c in config.get("calendar_ids", ())),
    )
    return AppleCalDavConnector(resolved)


def _default_secrets_loader() -> SecretsResolver:
    """Build the production :class:`SecretsLoader` lazily.

    Local import keeps the connector module importable without paying
    for the loader's env-snapshot work on every kairix import. The
    loader's resolution chain (env -> legacy aliases -> KV mount ->
    legacy chain) is unchanged from the canonical secrets package.
    """
    from kairix.secrets import SecretsLoader

    return SecretsLoader()
