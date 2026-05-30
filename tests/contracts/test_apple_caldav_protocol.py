"""Contract test for the Apple iCloud CalDAV connector plugin (F43).

Exercises the canonical fake (:class:`tests.fakes.FakeAppleCalDavConnector`)
AND the real implementation
(:class:`kairix.connectors.apple_caldav.AppleCalDavConnector`)
through the same :class:`~kairix.core.protocols.SourceConnector`
Protocol assertions. F43 requires this pairing — without it the fake
can drift away from the real wire (or vice versa) and the production
path silently diverges from what BDD / unit tests measure.

Real-impl path drives a scripted in-memory CalDAV client so no real
iCloud or HTTP traffic fires. The contract assertions check shape
(typed return values, name, sensitivity tier), not delivery latency.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from kairix.connectors.apple_caldav import (
    AppleCalDavClient,
    AppleCalDavConfig,
    AppleCalDavConnector,
    CalDavCalendarRef,
    CalendarEventRecord,
    CalendarSyncPage,
)
from kairix.core.protocols import ChangeEvent, RawArtefact, SourceConnector
from tests.fakes import FakeAppleCalDavConnector

_FIXTURE_CALENDAR_URL = "https://p01-caldav.icloud.com/12345/calendars/personal/"
_FIXTURE_EVENT_URL = _FIXTURE_CALENDAR_URL + "alpha-event.ics"


def _seed_record() -> CalendarEventRecord:
    return CalendarEventRecord(
        event_id="event-alpha",
        summary="Team sync",
        dtstart_iso="2026-05-25T09:00:00Z",
        dtend_iso="2026-05-25T10:00:00Z",
        location="Conference room",
        attendees=("agent-alpha@example.com",),
        organiser="agent-organiser@example.com",
        last_modified_iso="2026-05-25T08:00:00Z",
        recurrence_rule="FREQ=WEEKLY;COUNT=4",
        cancelled=False,
        removed=False,
        raw_ics="BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:event-alpha\nEND:VEVENT\nEND:VCALENDAR\n",
        event_url=_FIXTURE_EVENT_URL,
    )


def _seed_page() -> CalendarSyncPage:
    return CalendarSyncPage(events=(_seed_record(),), sync_token="caldav-sync-token-fresh")


class _ScriptedClient(AppleCalDavClient):
    """Scripted in-memory CalDAV client used by the real-impl factory."""

    def __init__(self) -> None:
        # Skip the real __init__ — no auth, no caldav library import.
        self._username = "agent-alpha@example.com"
        self._password = "fixture-app-password"  # pragma: allowlist secret — test fixture
        self._endpoint = "https://caldav.icloud.com"
        self._dav_client_factory = None
        self._dav_client = None

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
        return _seed_page()

    def fetch(self, event_url: str) -> CalendarEventRecord:
        del event_url
        return _seed_record()


# ---------------------------------------------------------------------------
# Factories — each yields a fresh SourceConnector for one test.
# ---------------------------------------------------------------------------


def _fake_factory() -> SourceConnector:
    """Canonical fake factory — seeds one created event + content."""
    return FakeAppleCalDavConnector(
        events=[
            ChangeEvent(op="created", item_id="event-alpha", modified_at="2026-05-25T08:00:00Z"),
        ],
        content={"event-alpha": b"BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:event-alpha\nEND:VEVENT\nEND:VCALENDAR\n"},
        sync_token="caldav-sync-token-fresh",
    )


def _real_factory() -> SourceConnector:
    """Real-impl factory — backed by a scripted in-memory CalDAV client."""
    config = AppleCalDavConfig(
        username="agent-alpha@example.com",
        password="fixture-app-password",  # pragma: allowlist secret — test fixture
    )
    return AppleCalDavConnector(
        config,
        client_factory=lambda _c: _ScriptedClient(),
    )


_FACTORIES: list[tuple[str, Callable[[], SourceConnector]]] = [
    ("fake", _fake_factory),
    ("real", _real_factory),
]


# ---------------------------------------------------------------------------
# Contract assertions — both implementations must satisfy each one.
# ---------------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_apple_caldav_connector_satisfies_source_connector_protocol(
    name: str, factory: Callable[[], SourceConnector]
) -> None:
    """F43: both fake and real impl satisfy the runtime-checkable Protocol.

    Sabotage-proof: removing ``list_changes`` from
    :class:`AppleCalDavConnector` flips the real-impl isinstance check
    to False; deleting the corresponding attribute from
    :class:`FakeAppleCalDavConnector` flips the fake check to False.
    """
    connector = factory()
    assert isinstance(connector, SourceConnector), f"{name!r} factory output is not a SourceConnector"
    assert connector.name == "apple_caldav"


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_apple_caldav_connector_list_changes_returns_change_events(
    name: str, factory: Callable[[], SourceConnector]
) -> None:
    """Both implementations stream :class:`ChangeEvent` instances.

    Sabotage-proof: the real impl mutated to return ``[None]`` from
    ``list_changes`` flunks the isinstance loop below; the fake
    mutated to yield ``{"op": "created"}`` dicts flunks the same loop.
    """
    connector = factory()
    events = list(connector.list_changes(cursor=None))
    assert events, f"{name!r} factory produced no events"
    for ev in events:
        assert isinstance(ev, ChangeEvent), f"{name!r} yielded a non-ChangeEvent: {ev!r}"
        assert ev.op in ("created", "modified", "deleted")
        assert ev.item_id, f"{name!r} yielded an event with empty item_id"


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_apple_caldav_connector_fetch_returns_raw_artefact(name: str, factory: Callable[[], SourceConnector]) -> None:
    """Both implementations satisfy the ``fetch`` -> :class:`RawArtefact` shape.

    For the real impl, ``fetch`` requires the item id to have surfaced
    through a prior ``list_changes`` call (the connector caches the
    ICS payload during sync). The contract test drives that pre-call
    so the real impl's cache is warm.

    Sabotage-proof: returning a tuple from ``fetch`` flunks the
    isinstance assertion for both impls.
    """
    connector = factory()
    # Warm the real impl's payload cache by draining list_changes first.
    list(connector.list_changes(cursor=None))

    artefact = connector.fetch("event-alpha")
    assert isinstance(artefact, RawArtefact)
    assert artefact.mime == "text/calendar"
    assert b"event-alpha" in artefact.raw, f"{name!r} fetch returned payload missing event id: {artefact.raw!r}"


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_apple_caldav_connector_source_link_round_trips_caldav_scheme(
    name: str, factory: Callable[[], SourceConnector]
) -> None:
    """``source_link`` returns a CalDAV-scheme link on both impls.

    Real-impl: when the connector has cached the event URL during a
    prior ``list_changes`` drain, the link is the actual CalDAV URL.
    Fake: the link is the fallback ``caldav://<id>`` shape.

    Sabotage-proof: hard-code the real impl to return an empty string —
    both assertions then fail.
    """
    connector = factory()
    # Warm the real impl's metadata cache.
    list(connector.list_changes(cursor=None))
    link = connector.source_link("event-alpha")
    assert link, f"{name!r} produced an empty source_link"
    # Real impl returns the captured event URL; fake returns the
    # caldav:// fallback. Either shape is contract-valid — both encode
    # the item identifier in some operator-clickable way.
    assert "event-alpha" in link or "alpha-event" in link, f"{name!r} source_link does not reference item id: {link!r}"


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_apple_caldav_connector_sensitivity_for_returns_personal_default(
    name: str, factory: Callable[[], SourceConnector]
) -> None:
    """``sensitivity_for`` returns the connector's configured tier (``personal``).

    Sabotage-proof: mutate the real impl to return ``"public"`` — the
    assertion below fails because the factory configured ``"personal"``.
    """
    connector = factory()
    tier = connector.sensitivity_for("event-alpha")
    assert tier == "personal", f"{name!r} returned unexpected sensitivity: {tier!r}"
