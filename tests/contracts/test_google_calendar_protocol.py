"""Contract test for the Google Calendar connector plugin (F43).

Exercises the canonical fake
(:class:`tests.fakes.FakeGoogleCalendarConnector`) AND the real
implementation
(:class:`kairix.connectors.google_calendar.GoogleCalendarConnector`)
through the same :class:`~kairix.core.protocols.SourceConnector`
Protocol assertions. F43 requires this pairing — without it the fake
can drift away from the real wire (or vice versa) and the production
path silently diverges from what BDD / unit tests measure.

Real-impl path drives a scripted in-memory client so no real Google
API traffic fires. The contract assertions check shape (typed return
values, name, sensitivity tier), not delivery latency.

Sabotage proof: dropping ``list_changes`` from
:class:`GoogleCalendarConnector` flips the real-impl isinstance check
to False; deleting the corresponding attribute from
:class:`FakeGoogleCalendarConnector` flips the fake check to False.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from kairix.connectors.google_calendar import (
    GoogleCalendarClient,
    GoogleCalendarConfig,
    GoogleCalendarConnector,
    GoogleCalendarEventRecord,
    GoogleCalendarEventsPage,
)
from kairix.core.protocols import ChangeEvent, RawArtefact, SourceConnector
from tests.fakes import FakeGoogleCalendarConnector


def _seed_page() -> GoogleCalendarEventsPage:
    record = GoogleCalendarEventRecord(
        event_id="event-alpha",
        summary="Sync",
        description="Standup notes at https://docs.example.com/agenda",
        start_iso="2026-05-25T09:00:00Z",
        end_iso="2026-05-25T10:00:00Z",
        location="Conference room",
        attendees=("agent-alpha@example.com",),
        organizer_email="agent-beta@example.com",
        updated_iso="2026-05-25T08:00:00Z",
        recurrence=(),
        status="confirmed",
        html_link="https://calendar.google.com/event?eid=event-alpha",
        raw_payload='{"id": "event-alpha"}',
    )
    return GoogleCalendarEventsPage(
        events=(record,),
        next_page_token=None,
        next_sync_token="next-sync-token-alpha",
    )


class _ScriptedClient(GoogleCalendarClient):
    """Scripted in-memory Google client used by the real-impl factory."""

    def __init__(self, page: GoogleCalendarEventsPage) -> None:
        self._page = page
        self._calendar_id = "primary"
        self._http = None  # type: ignore[assignment]  # F3 rationale: scripted client never makes HTTP calls
        self._page_size = 50

    def fetch_initial_events(self, _time_min_iso: str) -> GoogleCalendarEventsPage:
        return self._page

    def fetch_delta_events(self, _sync_token: str) -> GoogleCalendarEventsPage:
        return self._page

    def fetch_next_page_initial(self, _time_min_iso: str, _page_token: str) -> GoogleCalendarEventsPage:
        return self._page

    def fetch_next_page_delta(self, _sync_token: str, _page_token: str) -> GoogleCalendarEventsPage:
        return self._page

    def close(self) -> None:
        # Intentionally empty — scripted client owns no resources.
        return None


# ---------------------------------------------------------------------------
# Factories — each yields a fresh SourceConnector for one test.
# ---------------------------------------------------------------------------


def _fake_factory() -> SourceConnector:
    """Canonical fake factory — seeds one created event + content."""
    return FakeGoogleCalendarConnector(
        events=[
            ChangeEvent(op="created", item_id="event-alpha", modified_at="2026-05-25T08:00:00Z"),
        ],
        content={"event-alpha": b"Title: Sync"},
        sync_token="next-sync-token-alpha",
    )


def _real_factory() -> SourceConnector:
    """Real-impl factory — backed by a scripted in-memory Google client."""
    config = GoogleCalendarConfig(
        access_token="placeholder-token",  # pragma: allowlist secret — test fixture
    )
    return GoogleCalendarConnector(
        config,
        client_factory=lambda _c: _ScriptedClient(_seed_page()),
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
def test_connector_satisfies_source_connector_protocol(name: str, factory: Callable[[], SourceConnector]) -> None:
    """F43: both fake and real impl satisfy the runtime-checkable Protocol.

    Sabotage-proof: removing ``list_changes`` from
    :class:`GoogleCalendarConnector` flips the real-impl isinstance check
    to False; deleting the corresponding attribute from
    :class:`FakeGoogleCalendarConnector` flips the fake check to False.
    """
    connector = factory()
    assert isinstance(connector, SourceConnector), f"{name!r} factory output is not a SourceConnector"
    assert connector.name == "google_calendar"


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_connector_list_changes_returns_change_events(name: str, factory: Callable[[], SourceConnector]) -> None:
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
def test_connector_fetch_returns_raw_artefact(name: str, factory: Callable[[], SourceConnector]) -> None:
    """Both implementations satisfy the ``fetch`` -> :class:`RawArtefact` shape.

    For the real impl, ``fetch`` requires the item id to have surfaced
    through a prior ``list_changes`` call (the connector caches the
    rendered event body during sync). The contract test drives that
    pre-call so the real impl's cache is warm.

    Sabotage-proof: returning a tuple from ``fetch`` flunks the
    isinstance assertion for both impls.
    """
    connector = factory()
    # Warm the real impl's body cache by draining list_changes first.
    list(connector.list_changes(cursor=None))

    artefact = connector.fetch("event-alpha")
    assert isinstance(artefact, RawArtefact)
    assert artefact.mime == "text/calendar"
    assert artefact.raw, f"{name!r} fetch returned empty payload"


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_connector_source_link_round_trips_to_google_scheme(name: str, factory: Callable[[], SourceConnector]) -> None:
    """``source_link`` returns a Google Calendar URL on both impls.

    Sabotage-proof: hard-code the real impl to return an empty string —
    both ``startswith`` / membership assertions fail.
    """
    connector = factory()
    # Warm the cache so the real impl returns the htmlLink when set.
    list(connector.list_changes(cursor=None))
    link = connector.source_link("event-alpha")
    assert link.startswith("https://calendar.google.com/") or link.startswith("https://www.google.com/calendar/"), (
        f"{name!r} produced unexpected link: {link!r}"
    )
    assert "event-alpha" in link or "event?eid=event-alpha" in link, f"{name!r} link does not carry item_id: {link!r}"


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_connector_sensitivity_for_returns_configured_tier(name: str, factory: Callable[[], SourceConnector]) -> None:
    """``sensitivity_for`` returns the connector's configured tier.

    Sabotage-proof: mutate the real impl to return ``"public"`` — the
    assertion below fails because the factory configured ``"internal"``.
    """
    connector = factory()
    tier = connector.sensitivity_for("event-alpha")
    assert tier == "internal", f"{name!r} returned unexpected sensitivity: {tier!r}"


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_connector_next_cursor_returns_persistable_token(name: str, factory: Callable[[], SourceConnector]) -> None:
    """``next_cursor`` returns the Google ``nextSyncToken`` after a drain.

    Sabotage-proof: zeroing ``self._last_sync_token`` after each drain
    on the real impl makes this test fail with None.
    """
    connector = factory()
    list(connector.list_changes(cursor=None))
    token = connector.next_cursor()
    assert token == "next-sync-token-alpha", f"{name!r} returned unexpected cursor: {token!r}"
