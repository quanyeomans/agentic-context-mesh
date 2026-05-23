"""Contract test for the M365 calendar connector plugin (F43).

Exercises the canonical fake (:class:`tests.fakes.FakeM365CalendarConnector`)
AND the real implementation
(:class:`kairix.connectors.m365_calendar.M365CalendarConnector`)
through the same :class:`~kairix.core.protocols.SourceConnector`
Protocol assertions. F43 requires this pairing — without it the fake
can drift away from the real wire (or vice versa) and the production
path silently diverges from what BDD / unit tests measure.

Real-impl path drives a scripted in-memory client so no real Graph or
OAuth2 traffic fires. The contract assertions check shape (typed
return values, name, sensitivity tier), not delivery latency.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from kairix.connectors.m365_calendar import M365CalendarConnector
from kairix.connectors.m365_calendar.connector import M365CalendarConfig
from kairix.connectors.m365_calendar.graph_client import (
    CalendarDeltaPage,
    CalendarEventRecord,
    M365GraphCalendarClient,
)
from kairix.core.protocols import ChangeEvent, RawArtefact, SourceConnector
from tests.fakes import FakeM365CalendarConnector


def _seed_page() -> CalendarDeltaPage:
    record = CalendarEventRecord(
        event_id="event-alpha",
        subject="Sync",
        start_iso="2026-05-25T09:00:00Z",
        end_iso="2026-05-25T10:00:00Z",
        location="Conference room",
        attendees=("alpha@example.com",),
        organiser="organiser@example.com",
        last_modified_iso="2026-05-25T08:00:00Z",
        cancelled=False,
        removed=False,
        raw_payload='{"id": "event-alpha"}',
    )
    return CalendarDeltaPage(
        events=(record,),
        next_link=None,
        delta_link="https://graph.microsoft.com/v1.0/.../$deltatoken=alpha",
    )


class _ScriptedClient(M365GraphCalendarClient):
    """Scripted in-memory Graph client used by the real-impl factory."""

    def __init__(self, page: CalendarDeltaPage) -> None:
        self._page = page
        self._user_id = "operator@example.com"
        self._http = None  # type: ignore[assignment]  # F3 rationale: scripted client never makes HTTP calls
        self._page_size = 50

    def fetch_initial_delta(self, _start_iso: str, _end_iso: str) -> CalendarDeltaPage:
        return self._page

    def fetch_delta_page(self, _link: str) -> CalendarDeltaPage:
        return self._page

    def close(self) -> None:
        # Intentionally empty — scripted client owns no resources.
        return None


# ---------------------------------------------------------------------------
# Factories — each yields a fresh SourceConnector for one test.
# ---------------------------------------------------------------------------


def _fake_factory() -> SourceConnector:
    """Canonical fake factory — seeds one created event + content."""
    return FakeM365CalendarConnector(
        events=[
            ChangeEvent(op="created", item_id="event-alpha", modified_at="2026-05-25T08:00:00Z"),
        ],
        content={"event-alpha": b'{"id": "event-alpha"}'},
        delta_link="https://graph.microsoft.com/v1.0/.../$deltatoken=alpha",
    )


def _real_factory() -> SourceConnector:
    """Real-impl factory — backed by a scripted in-memory Graph client."""
    config = M365CalendarConfig(
        user_id="operator@example.com",
        tenant_id="placeholder-tenant",
        client_id="placeholder-client",
        client_secret="placeholder-secret",  # pragma: allowlist secret
    )
    return M365CalendarConnector(
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
    :class:`M365CalendarConnector` flips the real-impl isinstance check
    to False; deleting the corresponding attribute from
    :class:`FakeM365CalendarConnector` flips the fake check to False.
    """
    connector = factory()
    assert isinstance(connector, SourceConnector), f"{name!r} factory output is not a SourceConnector"
    assert connector.name == "m365_calendar"


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
    Graph payload during sync). The contract test drives that pre-call
    so the real impl's cache is warm.

    Sabotage-proof: returning a tuple from ``fetch`` flunks the
    isinstance assertion for both impls.
    """
    connector = factory()
    # Warm the real impl's payload cache by draining list_changes first.
    list(connector.list_changes(cursor=None))

    artefact = connector.fetch("event-alpha")
    assert isinstance(artefact, RawArtefact)
    assert artefact.mime == "application/json"
    assert b"event-alpha" in artefact.raw, f"{name!r} fetch returned payload missing event id: {artefact.raw!r}"


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_connector_source_link_round_trips_to_outlook_scheme(name: str, factory: Callable[[], SourceConnector]) -> None:
    """``source_link`` returns an Outlook web URL on both impls.

    Sabotage-proof: hard-code the real impl to return an empty string —
    both ``startswith`` assertions then fail.
    """
    connector = factory()
    link = connector.source_link("event-alpha")
    assert link.startswith("https://outlook.office.com/calendar/item/"), f"{name!r} produced unexpected link: {link!r}"
    assert "event-alpha" in link, f"{name!r} link does not carry item_id: {link!r}"


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
