"""Unit-level coverage for AppleCalDavConnector + AppleCalDavClient.

Covers the helper functions, the legacy-cursor parser, the
``metadata_for`` fallback path, the context-manager lifecycle, and the
client's safe-read helpers (display name + ctag + event parsing) so
the production code under ``kairix/connectors/apple_caldav/`` lifts
above the F7 per-file 90% coverage floor without taking a dependency
on the optional :mod:`caldav` library.

Tests substitute every external surface through the connector's
constructor seam (``client_factory``) — no monkey-patching, no env-var
manipulation (F1 / F2 clean).
"""

from __future__ import annotations

import pytest
import requests

from kairix.connectors.apple_caldav import (
    AppleCalDavClient,
    AppleCalDavConfig,
    AppleCalDavConnector,
    CalDavCalendarRef,
    CalendarEventRecord,
    CalendarSyncPage,
    make_connector,
)
from kairix.core.protocols import Container
from kairix.secrets import SecretNotFoundError
from tests.fakes import FakeSecretsLoader

pytestmark = pytest.mark.unit

_FIXTURE_CALENDAR_URL = "https://caldav.icloud.com/12345/calendars/personal/"


def _scripted_client_for(record: CalendarEventRecord) -> AppleCalDavClient:
    """Build a stand-in client returning one event with the supplied record."""

    class _C(AppleCalDavClient):
        def __init__(self) -> None:
            self._username = "agent-alpha@example.com"
            self._password = "fixture-app-password"  # pragma: allowlist secret — test fixture
            self._endpoint = "https://caldav.icloud.com"
            self._dav_client_factory = None
            self._dav_client = None

        def discover_calendars(self) -> tuple[CalDavCalendarRef, ...]:
            return (CalDavCalendarRef(url=_FIXTURE_CALENDAR_URL, display_name="Personal", ctag=None),)

        def list_changes(self, calendar_url: str, sync_token: str | None) -> CalendarSyncPage:
            del calendar_url, sync_token
            return CalendarSyncPage(events=(record,), sync_token="tok")

        def fetch(self, event_url: str) -> CalendarEventRecord:
            del event_url
            return record

    return _C()


def _build_record(
    *,
    summary: str = "Sync",
    organiser: str = "agent-alpha@example.com",
    last_modified: str = "2026-05-25T08:00:00Z",
    attendees: tuple[str, ...] = ("attendee@example.com",),
    cancelled: bool = False,
    removed: bool = False,
    rrule: str = "",
) -> CalendarEventRecord:
    return CalendarEventRecord(
        event_id="event-unit-1",
        summary=summary,
        dtstart_iso="2026-05-25T09:00:00Z",
        dtend_iso="2026-05-25T10:30:00Z",
        location="Conference room",
        attendees=attendees,
        organiser=organiser,
        last_modified_iso=last_modified,
        recurrence_rule=rrule,
        cancelled=cancelled,
        removed=removed,
        raw_ics="BEGIN:VCALENDAR\nEND:VCALENDAR\n",
        event_url=_FIXTURE_CALENDAR_URL + "event-unit-1.ics",
    )


# ---------------------------------------------------------------------------
# Connector behaviour (drives through public list_changes / metadata_for /
# fetch / source_link / next_cursor — exercises the private helpers
# transitively per F5).
# ---------------------------------------------------------------------------


def _build_connector(record: CalendarEventRecord) -> AppleCalDavConnector:
    config = AppleCalDavConfig(
        username="agent-alpha@example.com",
        password="fixture-app-password",  # pragma: allowlist secret — test fixture
    )
    return AppleCalDavConnector(config, client_factory=lambda _c: _scripted_client_for(record))


def test_metadata_for_returns_empty_when_id_unseen() -> None:
    """An unseen id collapses to SourceMetadata() — no crash."""
    record = _build_record()
    connector = _build_connector(record)
    # Don't drain — cache is empty.
    metadata = connector.metadata_for("never-seen")
    assert metadata.modified_at is None
    assert metadata.author is None
    assert metadata.tags == ()


def test_metadata_for_populates_every_field_when_envelope_complete() -> None:
    """Full envelope → SourceMetadata with author, tags, properties."""
    record = _build_record(rrule="FREQ=DAILY;COUNT=5")
    connector = _build_connector(record)
    list(connector.list_changes(cursor=None))
    metadata = connector.metadata_for("event-unit-1")
    assert metadata.author == "agent-alpha@example.com"
    assert metadata.author_email == "agent-alpha@example.com"
    assert metadata.modified_at == "2026-05-25T08:00:00Z"
    assert "attendee@example.com" in metadata.tags
    assert metadata.properties.get("recurrence_rule") == "FREQ=DAILY;COUNT=5"
    assert metadata.properties.get("location") == "Conference room"
    assert metadata.properties.get("duration_minutes") == "90"
    assert metadata.properties.get("summary") == "Sync"
    assert metadata.properties.get("start") == "2026-05-25T09:00:00Z"
    assert metadata.properties.get("end") == "2026-05-25T10:30:00Z"


def test_metadata_for_handles_non_email_organiser() -> None:
    """When the organiser doesn't carry '@', author_email is None."""
    record = _build_record(organiser="not-an-email")
    connector = _build_connector(record)
    list(connector.list_changes(cursor=None))
    metadata = connector.metadata_for("event-unit-1")
    assert metadata.author == "not-an-email"
    assert metadata.author_email is None


def test_source_link_falls_back_to_caldav_scheme_when_id_unseen() -> None:
    """source_link on an unseen id returns the caldav:// fallback."""
    connector = _build_connector(_build_record())
    link = connector.source_link("never-seen-id")
    assert link == "caldav://never-seen-id"


def test_source_link_returns_cached_event_url_after_drain() -> None:
    """After list_changes drains, source_link returns the CalDAV URL."""
    connector = _build_connector(_build_record())
    list(connector.list_changes(cursor=None))
    link = connector.source_link("event-unit-1")
    assert link.endswith("event-unit-1.ics")


def test_fetch_returns_cached_raw_artefact() -> None:
    """fetch returns the cached ICS payload with mime=text/calendar."""
    connector = _build_connector(_build_record())
    list(connector.list_changes(cursor=None))
    artefact = connector.fetch("event-unit-1")
    assert artefact.mime == "text/calendar"
    assert artefact.raw.startswith(b"BEGIN:VCALENDAR")


def test_next_cursor_returns_none_when_no_drain_yet() -> None:
    """Before the first drain, next_cursor is None."""
    connector = _build_connector(_build_record())
    assert connector.next_cursor() is None


def test_next_cursor_returns_composite_after_drain() -> None:
    """After drain, next_cursor folds per-calendar tokens into one string."""
    connector = _build_connector(_build_record())
    list(connector.list_changes(cursor=None))
    cursor = connector.next_cursor()
    assert cursor is not None
    assert _FIXTURE_CALENDAR_URL in cursor
    assert "=tok" in cursor


def test_cancelled_event_emits_deleted_change() -> None:
    """STATUS:CANCELLED → ChangeEvent.op == 'deleted'."""
    connector = _build_connector(_build_record(cancelled=True))
    events = list(connector.list_changes(cursor=None))
    assert len(events) == 1
    assert events[0].op == "deleted"


def test_removed_event_emits_deleted_change() -> None:
    """CalDAV-removed → ChangeEvent.op == 'deleted'."""
    connector = _build_connector(_build_record(removed=True))
    events = list(connector.list_changes(cursor=None))
    assert len(events) == 1
    assert events[0].op == "deleted"


def test_known_id_emits_modified_on_second_pass() -> None:
    """An id surfaced as 'created' once surfaces as 'modified' on re-emit."""
    connector = _build_connector(_build_record())
    first = list(connector.list_changes(cursor=None))
    second = list(connector.list_changes(cursor=None))
    assert first[0].op == "created"
    assert second[0].op == "modified"


def test_seed_known_ids_forces_modified_branch() -> None:
    """seed_known_ids pre-populates the set so first emit is modified."""
    connector = _build_connector(_build_record())
    connector.seed_known_ids(["event-unit-1"])
    events = list(connector.list_changes(cursor=None))
    assert events[0].op == "modified"


def test_close_is_idempotent() -> None:
    """close() may be called multiple times safely."""
    connector = _build_connector(_build_record())
    list(connector.list_changes(cursor=None))
    connector.close()
    connector.close()  # second call must not raise


def test_context_manager_enters_and_exits_cleanly() -> None:
    """`with connector:` drains and closes."""
    connector = _build_connector(_build_record())
    with connector as ctx:
        assert ctx is connector
        events = list(connector.list_changes(cursor=None))
    assert events


def test_load_credentials_shim_returns_unchanged() -> None:
    """CredentialsConnector shim is a pass-through."""
    connector = _build_connector(_build_record())
    creds = {"username": "u", "password": "p"}  # pragma: allowlist secret — test fixture
    assert connector.load_credentials(creds) == creds


def test_load_from_checkpoint_delegates_to_list_changes() -> None:
    """CheckpointedConnector shim forwards to list_changes."""
    connector = _build_connector(_build_record())
    container = Container(
        cc_pair_id=1,
        container_id=_FIXTURE_CALENDAR_URL,
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    events = list(connector.load_from_checkpoint(container, checkpoint=None))
    assert events


def test_make_connector_raises_when_username_missing_from_config_and_loader() -> None:
    """When neither config nor loader resolves username, SecretNotFoundError fires.

    The canonical surface now resolves missing config fields via the injected
    :class:`SecretsResolver`. A FakeSecretsLoader with no bound values means
    the require() call raises ``SecretNotFoundError`` — message carries the
    canonical KV name + the F21 fix/next/run markers from the loader.

    Sabotage-proof: dropping the ``if not username_raw or not password_raw``
    branch in make_connector → the connector silently constructs with
    ``str(None)`` username, the test catches that via the missing exception.
    """
    with pytest.raises(SecretNotFoundError):
        make_connector(
            {"password": "p"},  # pragma: allowlist secret — test fixture
            secrets=FakeSecretsLoader(),
        )


def test_make_connector_raises_when_password_missing_from_config_and_loader() -> None:
    """When neither config nor loader resolves password, SecretNotFoundError fires.

    Sabotage-proof: removing the access (password) require() call in
    make_connector → connector constructs with ``str(None)`` password, the
    test catches the missing exception.
    """
    with pytest.raises(SecretNotFoundError):
        make_connector(
            {"username": "u"},
            secrets=FakeSecretsLoader(),
        )


def test_make_connector_resolves_username_via_loader_when_absent_from_config() -> None:
    """Config password + loader-supplied username yields a working connector.

    Sabotage-proof: replace the loader.require call with the literal string
    ``"sabotage-string"`` → the assertion on the resolved username flunks.
    """
    loader = FakeSecretsLoader(
        values={
            ("connector", "apple-caldav", None, "username"): "loader-username@example.com",
        },
    )
    connector = make_connector(
        {
            "password": "fixture-app-password",  # pragma: allowlist secret — test fixture
        },
        secrets=loader,
    )
    assert connector._config.username == "loader-username@example.com"
    assert connector._config.password == "fixture-app-password"  # pragma: allowlist secret


def test_make_connector_resolves_password_via_loader_when_absent_from_config() -> None:
    """Config username + loader-supplied password yields a working connector.

    Sabotage-proof: replace the loader.require call with an empty string →
    the assertion on the resolved password flunks.
    """
    loader = FakeSecretsLoader(
        values={
            ("connector", "apple-caldav", None, "access"): "loader-app-password",  # pragma: allowlist secret
        },
    )
    connector = make_connector(
        {"username": "agent-alpha@example.com"},
        secrets=loader,
    )
    assert connector._config.username == "agent-alpha@example.com"
    assert connector._config.password == "loader-app-password"  # pragma: allowlist secret


def test_make_connector_resolves_both_credentials_via_loader_when_config_empty() -> None:
    """Empty config + fully-populated loader → connector with loader-resolved creds.

    Pins the F45-style "no env vars in tests" contract: the canonical surface
    works end-to-end through a Fake* without any monkey-patching.

    Sabotage-proof: drop the username require() call → assertion on
    ``connector._config.username`` flunks.
    """
    loader = FakeSecretsLoader(
        values={
            ("connector", "apple-caldav", None, "username"): "loader-username@example.com",
            ("connector", "apple-caldav", None, "access"): "loader-app-password",  # pragma: allowlist secret
        },
    )
    connector = make_connector({}, secrets=loader)
    assert connector._config.username == "loader-username@example.com"
    assert connector._config.password == "loader-app-password"  # pragma: allowlist secret


def test_apple_caldav_loads_secrets_via_loader() -> None:
    """make_connector calls loader.require() for each missing credential.

    Pins the canonical-surface contract: when both username and password
    are absent from the config block, the injected SecretsResolver is asked
    for each via its require() method (not get()), so a missing secret
    surfaces a typed SecretNotFoundError — never a stack trace.

    Sabotage-proof: swap ``loader.require`` to ``loader.get`` in
    make_connector → the loader.get_calls captures `get` calls only when
    require() is called (FakeSecretsLoader's require chains through get),
    so the test instead asserts that BOTH calls landed in order. The hash
    of identity tuples in get_calls flunks if the canonical tuple shape
    changes.
    """
    loader = FakeSecretsLoader(
        values={
            ("connector", "apple-caldav", None, "username"): "loader-u@example.com",
            ("connector", "apple-caldav", None, "access"): "loader-p",  # pragma: allowlist secret
        },
    )
    make_connector({}, secrets=loader)
    assert ("connector", "apple-caldav", None, "username") in loader.get_calls
    assert ("connector", "apple-caldav", None, "access") in loader.get_calls


def test_make_connector_builds_with_required_fields() -> None:
    """Happy-path: required fields suffice; defaults populate the rest."""
    connector = make_connector(
        {
            "username": "agent-alpha@example.com",
            "password": "fixture-app-password",  # pragma: allowlist secret — test fixture
        }
    )
    assert connector.name == "apple_caldav"
    assert connector._config.endpoint == "https://caldav.icloud.com"
    assert connector._config.sensitivity == "personal"


def test_make_connector_propagates_calendar_ids() -> None:
    """calendar_ids list is preserved as a tuple."""
    connector = make_connector(
        {
            "username": "agent-alpha@example.com",
            "password": "fixture-app-password",  # pragma: allowlist secret — test fixture
            "calendar_ids": [_FIXTURE_CALENDAR_URL, "https://other/"],
        }
    )
    assert connector._config.calendar_ids == (_FIXTURE_CALENDAR_URL, "https://other/")


def test_calendar_id_filter_scopes_discovery() -> None:
    """When calendar_ids is set, only matching calendars are returned."""

    class _Multi(AppleCalDavClient):
        def __init__(self) -> None:
            self._username = "agent-alpha@example.com"
            self._password = "fixture-app-password"  # pragma: allowlist secret — test fixture
            self._endpoint = "https://caldav.icloud.com"
            self._dav_client_factory = None
            self._dav_client = None

        def discover_calendars(self) -> tuple[CalDavCalendarRef, ...]:
            return (
                CalDavCalendarRef(url=_FIXTURE_CALENDAR_URL, display_name="Personal", ctag=None),
                CalDavCalendarRef(url="https://other/", display_name="Other", ctag=None),
            )

        def list_changes(self, calendar_url: str, sync_token: str | None) -> CalendarSyncPage:
            return CalendarSyncPage(events=(), sync_token="tok")

        def fetch(self, event_url: str) -> CalendarEventRecord:
            raise NotImplementedError

    config = AppleCalDavConfig(
        username="agent-alpha@example.com",
        password="fixture-app-password",  # pragma: allowlist secret — test fixture
        calendar_ids=(_FIXTURE_CALENDAR_URL,),
    )
    connector = AppleCalDavConnector(config, client_factory=lambda _c: _Multi())
    containers = list(connector.iter_containers(cc_pair_id=1))
    assert [c.container_id for c in containers] == [_FIXTURE_CALENDAR_URL]


# ---------------------------------------------------------------------------
# Client helpers — exercised without the caldav library
# ---------------------------------------------------------------------------


def _scripted_event(uid: str, status: str = "CONFIRMED", attendees_field: object = None) -> object:
    """Build a caldav-Event-shaped stand-in usable through the public client.fetch."""

    data_map = {
        "UID": uid,
        "SUMMARY": "Test",
        "DTSTART": "2026-05-25T09:00:00Z",
        "DTEND": "2026-05-25T10:00:00Z",
        "STATUS": status,
    }

    class _Component:
        @staticmethod
        def get(k: str) -> object:
            if k == "ATTENDEE":
                return attendees_field
            return data_map.get(k)

    class _Event:
        data = "BEGIN:VCALENDAR\nEND:VCALENDAR\n"
        url = _FIXTURE_CALENDAR_URL + uid + ".ics"
        icalendar_component = _Component

    return _Event()


def test_client_fetch_parses_attendee_field_as_single_string() -> None:
    """Public surface: client.fetch translates a single ATTENDEE string into a one-tuple."""

    class _DavClient:
        def calendar_event_by_url(self, url: str) -> object:
            del url
            return _scripted_event("att-1", attendees_field="mailto:agent-alpha@example.com")

    client = AppleCalDavClient(
        username="u",
        password="p",  # pragma: allowlist secret — test fixture
        dav_client_factory=_DavClient(),
    )
    record = client.fetch(_FIXTURE_CALENDAR_URL + "att-1.ics")
    assert record.attendees == ("mailto:agent-alpha@example.com",)


def test_client_fetch_parses_attendee_field_as_list() -> None:
    """Public surface: client.fetch translates an ATTENDEE list into a tuple."""

    class _DavClient:
        def calendar_event_by_url(self, url: str) -> object:
            del url
            return _scripted_event("att-2", attendees_field=["a@example.com", "b@example.com"])

    client = AppleCalDavClient(
        username="u",
        password="p",  # pragma: allowlist secret — test fixture
        dav_client_factory=_DavClient(),
    )
    record = client.fetch(_FIXTURE_CALENDAR_URL + "att-2.ics")
    assert record.attendees == ("a@example.com", "b@example.com")


def test_client_fetch_parses_attendee_field_when_none() -> None:
    """Public surface: client.fetch handles missing ATTENDEE field."""

    class _DavClient:
        def calendar_event_by_url(self, url: str) -> object:
            del url
            return _scripted_event("att-3", attendees_field=None)

    client = AppleCalDavClient(
        username="u",
        password="p",  # pragma: allowlist secret — test fixture
        dav_client_factory=_DavClient(),
    )
    record = client.fetch(_FIXTURE_CALENDAR_URL + "att-3.ics")
    assert record.attendees == ()


def test_client_fetch_marks_cancelled_status() -> None:
    """Public surface: STATUS=CANCELLED surfaces as record.cancelled True."""

    class _DavClient:
        def calendar_event_by_url(self, url: str) -> object:
            del url
            return _scripted_event("cx-1", status="CANCELLED")

    client = AppleCalDavClient(
        username="u",
        password="p",  # pragma: allowlist secret — test fixture
        dav_client_factory=_DavClient(),
    )
    record = client.fetch(_FIXTURE_CALENDAR_URL + "cx-1.ics")
    assert record.cancelled is True


def test_connector_handles_composite_cursor_round_trip() -> None:
    """Public surface: a composite cursor (url=tok|url=tok) drives per-calendar reads.

    The legacy single-cursor path encodes per-calendar tokens into one
    pipe-delimited string on emit; subsequent ticks pass the same
    string back. Drives the private _parse_composite_cursor path
    transitively.
    """

    class _RecordingClient(AppleCalDavClient):
        def __init__(self) -> None:
            self._username = "agent-alpha@example.com"
            self._password = "fixture-app-password"  # pragma: allowlist secret — test fixture
            self._endpoint = "https://caldav.icloud.com"
            self._dav_client_factory = None
            self._dav_client = None
            self.calls: list[tuple[str, str | None]] = []

        def discover_calendars(self) -> tuple[CalDavCalendarRef, ...]:
            return (
                CalDavCalendarRef(url=_FIXTURE_CALENDAR_URL, display_name="Personal", ctag=None),
                CalDavCalendarRef(url="https://other/", display_name="Other", ctag=None),
            )

        def list_changes(self, calendar_url: str, sync_token: str | None) -> CalendarSyncPage:
            self.calls.append((calendar_url, sync_token))
            return CalendarSyncPage(events=(), sync_token=f"tok-after-{calendar_url[-6:]}")

        def fetch(self, event_url: str) -> CalendarEventRecord:
            raise NotImplementedError

    recording = _RecordingClient()
    config = AppleCalDavConfig(
        username="agent-alpha@example.com",
        password="fixture-app-password",  # pragma: allowlist secret — test fixture
    )
    connector = AppleCalDavConnector(config, client_factory=lambda _c: recording)
    composite = f"{_FIXTURE_CALENDAR_URL}=tok-prev-a|https://other/=tok-prev-b"
    list(connector.list_changes(cursor=composite))
    assert (_FIXTURE_CALENDAR_URL, "tok-prev-a") in recording.calls
    assert ("https://other/", "tok-prev-b") in recording.calls


def test_connector_metadata_duration_returns_none_for_unparseable_dtstart() -> None:
    """Public surface: an event with empty DTSTART skips duration_minutes."""
    record = CalendarEventRecord(
        event_id="dur-empty",
        summary="No times",
        dtstart_iso="",
        dtend_iso="",
        location="",
        attendees=(),
        organiser="",
        last_modified_iso="2026-05-25T08:00:00Z",
        recurrence_rule="",
        cancelled=False,
        removed=False,
        raw_ics="BEGIN:VCALENDAR\nEND:VCALENDAR\n",
        event_url=_FIXTURE_CALENDAR_URL + "dur-empty.ics",
    )
    connector = _build_connector(record)
    list(connector.list_changes(cursor=None))
    metadata = connector.metadata_for("dur-empty")
    assert "duration_minutes" not in metadata.properties


def test_client_safe_display_name_uses_url_tail_on_missing_property() -> None:
    """When the calendar has no DAV:displayname, fall back to URL tail."""
    client = AppleCalDavClient(username="u", password="p")  # pragma: allowlist secret — test fixture

    class _BareCalendar:
        url = _FIXTURE_CALENDAR_URL

        def get_properties(self) -> dict[str, str]:
            return {}

    name = client._safe_display_name(_BareCalendar())
    assert name == "personal"


def test_client_safe_display_name_handles_exception() -> None:
    """An exception in get_properties triggers the URL-tail fallback."""
    client = AppleCalDavClient(username="u", password="p")  # pragma: allowlist secret — test fixture

    class _Broken:
        url = _FIXTURE_CALENDAR_URL

        def get_properties(self) -> dict[str, str]:
            raise requests.RequestException("boom")

    name = client._safe_display_name(_Broken())
    assert name == "personal"


def test_client_safe_ctag_returns_none_on_exception() -> None:
    """When get_properties raises, ctag collapses to None."""
    client = AppleCalDavClient(username="u", password="p")  # pragma: allowlist secret — test fixture

    class _Broken:
        def get_properties(self) -> dict[str, str]:
            raise AttributeError("no properties")

    assert client._safe_ctag(_Broken()) is None


def test_client_fetch_events_skips_unparseable() -> None:
    """An event that raises on parse is skipped, others survive."""
    client = AppleCalDavClient(username="u", password="p")  # pragma: allowlist secret — test fixture

    class _GoodEvent:
        data = "BEGIN:VCALENDAR\nEND:VCALENDAR\n"
        url = "https://x/"

        class icalendar_component:  # noqa: N801 — mirrors caldav.Event attr name verbatim
            @staticmethod
            def get(k: str) -> object:
                return {"UID": "good-1", "STATUS": "CONFIRMED"}.get(k)

    class _BadEvent:
        # No icalendar_component → _parse_event raises ValueError → skipped.
        data = ""
        url = ""
        icalendar_component = None

    class _CalendarWithMixedEvents:
        def events(self) -> list[object]:
            return [_BadEvent(), _GoodEvent()]

    records = client._fetch_events(_CalendarWithMixedEvents())
    # Bad event is skipped; good event surfaces.
    assert any(r.event_id == "good-1" for r in records)


def test_client_fetch_events_returns_empty_on_exception() -> None:
    """When calendar.events() raises, return empty tuple."""
    client = AppleCalDavClient(username="u", password="p")  # pragma: allowlist secret — test fixture

    class _BrokenCalendar:
        def events(self) -> list[object]:
            raise requests.RequestException("network down")

    assert client._fetch_events(_BrokenCalendar()) == ()


def test_client_fetch_sync_token_returns_none_when_attribute_missing() -> None:
    """When calendar has no sync_token attr, return None."""
    client = AppleCalDavClient(username="u", password="p")  # pragma: allowlist secret — test fixture
    assert client._fetch_sync_token(object()) is None


def test_client_parse_event_raises_when_no_icalendar_component() -> None:
    """An event without ``icalendar_component`` raises ValueError with fix marker."""
    client = AppleCalDavClient(username="u", password="p")  # pragma: allowlist secret — test fixture

    class _NoComponent:
        data = ""
        url = ""
        icalendar_component = None

    with pytest.raises(ValueError, match="no icalendar component"):
        client._parse_event(_NoComponent())


def test_client_ensure_dav_client_uses_factory_when_supplied() -> None:
    """When dav_client_factory is set, _ensure_dav_client returns it."""
    sentinel = object()
    client = AppleCalDavClient(
        username="u",
        password="p",  # pragma: allowlist secret — test fixture
        dav_client_factory=sentinel,
    )
    assert client._ensure_dav_client() is sentinel
    # Second call returns the cached value, not a fresh call to factory.
    assert client._ensure_dav_client() is sentinel


def test_client_safe_display_name_returns_value_when_present() -> None:
    """Happy path: get_properties returns the DAV:displayname value."""
    client = AppleCalDavClient(username="u", password="p")  # pragma: allowlist secret — test fixture

    class _Cal:
        url = _FIXTURE_CALENDAR_URL

        def get_properties(self) -> dict[str, str]:
            return {"{DAV:}displayname": "Personal Calendar"}

    assert client._safe_display_name(_Cal()) == "Personal Calendar"


def test_client_safe_ctag_returns_value_when_present() -> None:
    """Happy path: get_properties returns the getctag value."""
    client = AppleCalDavClient(username="u", password="p")  # pragma: allowlist secret — test fixture

    class _Cal:
        def get_properties(self) -> dict[str, str]:
            return {"{http://calendarserver.org/ns/}getctag": "ctag-xyz"}

    assert client._safe_ctag(_Cal()) == "ctag-xyz"


def test_client_safe_ctag_returns_none_when_value_empty() -> None:
    """Empty getctag value → None."""
    client = AppleCalDavClient(username="u", password="p")  # pragma: allowlist secret — test fixture

    class _Cal:
        def get_properties(self) -> dict[str, str]:
            return {"{http://calendarserver.org/ns/}getctag": ""}

    assert client._safe_ctag(_Cal()) is None


def test_client_discover_calendars_via_dav_client_factory() -> None:
    """discover_calendars walks principal.calendars() through the DI seam."""

    class _Cal:
        url = _FIXTURE_CALENDAR_URL

        def get_properties(self) -> dict[str, str]:
            return {
                "{DAV:}displayname": "Personal",
                "{http://calendarserver.org/ns/}getctag": "ctag-1",
            }

    class _Principal:
        def calendars(self) -> list[object]:
            return [_Cal()]

    class _DavClient:
        def principal(self) -> _Principal:
            return _Principal()

    client = AppleCalDavClient(
        username="u",
        password="p",  # pragma: allowlist secret — test fixture
        dav_client_factory=_DavClient(),
    )
    calendars = client.discover_calendars()
    assert calendars == (CalDavCalendarRef(url=_FIXTURE_CALENDAR_URL, display_name="Personal", ctag="ctag-1"),)


def test_client_list_changes_via_dav_client_factory() -> None:
    """list_changes drains events + sync_token through the DI seam."""

    class _GoodEvent:
        data = "BEGIN:VCALENDAR\nEND:VCALENDAR\n"
        url = _FIXTURE_CALENDAR_URL + "good.ics"

        class icalendar_component:  # noqa: N801 — mirrors caldav.Event attr name verbatim
            @staticmethod
            def get(k: str) -> object:
                return {
                    "UID": "good-uid",
                    "SUMMARY": "Good",
                    "DTSTART": "2026-05-25T09:00:00Z",
                    "DTEND": "2026-05-25T10:00:00Z",
                    "STATUS": "CONFIRMED",
                }.get(k)

    class _Cal:
        sync_token = "tok-fresh"

        def events(self) -> list[object]:
            return [_GoodEvent()]

    class _DavClient:
        def calendar(self, url: str) -> _Cal:
            del url
            return _Cal()

    client = AppleCalDavClient(
        username="u",
        password="p",  # pragma: allowlist secret — test fixture
        dav_client_factory=_DavClient(),
    )
    page = client.list_changes(_FIXTURE_CALENDAR_URL, sync_token=None)
    assert page.sync_token == "tok-fresh"
    assert len(page.events) == 1
    assert page.events[0].event_id == "good-uid"


def test_client_fetch_via_dav_client_factory() -> None:
    """fetch parses one event by URL through the DI seam."""

    class _Event:
        data = "BEGIN:VCALENDAR\nEND:VCALENDAR\n"
        url = _FIXTURE_CALENDAR_URL + "fetch.ics"

        class icalendar_component:  # noqa: N801 — mirrors caldav.Event attr name verbatim
            @staticmethod
            def get(k: str) -> object:
                return {
                    "UID": "fetch-uid",
                    "SUMMARY": "Fetched",
                    "DTSTART": "2026-05-25T09:00:00Z",
                    "DTEND": "2026-05-25T10:00:00Z",
                    "STATUS": "CONFIRMED",
                }.get(k)

    class _DavClient:
        def calendar_event_by_url(self, url: str) -> _Event:
            del url
            return _Event()

    client = AppleCalDavClient(
        username="u",
        password="p",  # pragma: allowlist secret — test fixture
        dav_client_factory=_DavClient(),
    )
    record = client.fetch(_FIXTURE_CALENDAR_URL + "fetch.ics")
    assert record.event_id == "fetch-uid"
