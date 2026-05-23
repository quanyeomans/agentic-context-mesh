"""End-to-end composed path test for the ``connector_m365_calendar`` flag.

F48 sibling to ``tests/e2e/test_composed_production_path.py``. Pinned
by F54 because the flag's ``related_spec`` references
``docs/architecture/connector-ingestion-architecture.md`` — a
top-level capability spec.

Exercises the composed production path with the flag ON:

  flag-resolver pins connector_m365_calendar=True
    → resolver lookup returns the connector in the enabled set
    → real M365CalendarConnector constructed with a scripted Graph
      client (no real OAuth2 exchange, no real network I/O)
    → connector.list_changes(None) drains the scripted page
    → assertions verify the connector exposed a persisted delta link
      AND surfaced the scripted event as a typed ChangeEvent

The OFF path is covered by the integration tests at
``tests/integration/test_feature_flag_connector_m365_calendar.py``.
F54's E2E requirement is per-flag (one E2E composed-path file); both
branches don't both need an E2E entry.

Sabotage proof (verified by the agent, restored on completion):
forcing ``client_factory`` to construct an unscripted client (deleting
the scripted client wiring) makes the ``list_changes`` call raise on
queue-exhaustion and the test fails on the AssertionError. Restored,
the composed path returns the seeded event.
"""

from __future__ import annotations

import pytest

from kairix.connectors.m365_calendar import M365CalendarConnector
from kairix.connectors.m365_calendar.connector import M365CalendarConfig
from kairix.connectors.m365_calendar.graph_client import (
    CalendarDeltaPage,
    CalendarEventRecord,
    M365GraphCalendarClient,
)
from kairix.core.protocols import ChangeEvent, SourceConnector
from tests.fakes import FakeFeatureFlagResolver


class _ScriptedClient(M365GraphCalendarClient):
    """Scripted Graph client — same shape as the integration test."""

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


def _seed_page() -> CalendarDeltaPage:
    record = CalendarEventRecord(
        event_id="event-cutover-trial",
        subject="M365 calendar cutover trial",
        start_iso="2026-05-25T09:00:00Z",
        end_iso="2026-05-25T10:00:00Z",
        location="Conference room",
        attendees=("alpha@example.com", "beta@example.com"),
        organiser="organiser@example.com",
        last_modified_iso="2026-05-25T08:00:00Z",
        cancelled=False,
        removed=False,
        raw_payload='{"id": "event-cutover-trial"}',
    )
    return CalendarDeltaPage(
        events=(record,),
        next_link=None,
        delta_link="https://graph.microsoft.com/v1.0/.../$deltatoken=cutover",
    )


@pytest.mark.e2e
def test_composed_connector_m365_calendar_on_path() -> None:
    """Flag ON, composed path: resolver → connector construction → list_changes.

    Verifies the full composed surface from the flag-resolver lookup
    through the typed ``SourceConnector`` Protocol contract and the
    delta-link cursor accessor — no monkey-patches, no skips.
    """
    resolver = FakeFeatureFlagResolver().with_flag("connector_m365_calendar", True)
    assert resolver.get("connector_m365_calendar") is True

    scripted_page = _seed_page()

    config = M365CalendarConfig(
        user_id="operator@example.com",
        tenant_id="placeholder-tenant",
        client_id="placeholder-client",
        client_secret="placeholder-secret",  # pragma: allowlist secret
    )
    connector = M365CalendarConnector(
        config,
        client_factory=lambda _c: _ScriptedClient(scripted_page),
    )

    # Protocol-shape assertion against the runtime-checkable Protocol —
    # mirrors the contract layer's pairing pattern.
    assert isinstance(connector, SourceConnector)
    assert connector.name == "m365_calendar"

    events = list(connector.list_changes(cursor=None))
    assert events, f"composed path must surface the scripted event; got {events!r}"

    typed_events = [e for e in events if isinstance(e, ChangeEvent)]
    assert typed_events == events, "composed path must emit typed ChangeEvent instances only"

    assert [(e.op, e.item_id) for e in events] == [("created", "event-cutover-trial")]
    assert connector.last_delta_link is not None, "composed path must expose a persisted delta link as the next cursor"
    assert "deltatoken=cutover" in connector.last_delta_link

    # Sensitivity default per ADR-005 — calendar events default to internal.
    assert connector.sensitivity_for("event-cutover-trial") == "internal"

    # Source link follows the Outlook web URL deep-link form.
    link = connector.source_link("event-cutover-trial")
    assert link.startswith("https://outlook.office.com/calendar/item/"), (
        f"composed path must emit an Outlook deep-link; got {link!r}"
    )
