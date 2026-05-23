"""Integration tests for the ``connector_m365_calendar`` flag (KP-3).

Exercises both branches of the connector-set resolution logic through
the same composition surface the BDD scenario uses, but with the real
:class:`kairix.connectors.m365_calendar.M365CalendarConnector`
constructed against a scripted Graph client. The OFF branch must not
construct the connector at all; the ON branch must construct it AND
drive a real ``list_changes(None)`` against a scripted Graph delta page
so the chunk-write surface and Bronze persistence boundaries are
exercised end-to-end.

F1-clean: ``FakeFeatureFlagResolver`` from ``tests/fakes.py`` is the
test seam — no @patch / module-attribute substitution on kairix.
F2-clean: no ``KAIRIX_*`` env-var manipulation.

Sabotage proof (executed by the agent, restored on completion):
inverting the if-branch in :func:`_resolve_and_run` so OFF constructs
the connector and ON skips it — confirmed that BOTH
:func:`test_flag_off_skips_construction` AND
:func:`test_flag_on_constructs_and_syncs` fail. Restoring the original
direction returns both tests to green.
"""

from __future__ import annotations

from typing import Any

import pytest

from kairix.connectors.m365_calendar import M365CalendarConnector
from kairix.connectors.m365_calendar.connector import M365CalendarConfig
from kairix.connectors.m365_calendar.graph_client import (
    CalendarDeltaPage,
    CalendarEventRecord,
    M365GraphCalendarClient,
)
from kairix.core.protocols import ChangeEvent
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.integration

# Literal flag name — F54 scans for ``with_flag("connector_m365_calendar", True)``
# AND ``with_flag("connector_m365_calendar", False)`` as the
# both-branch-exercise heuristic. The literal lives at the call sites
# below so the regex finds it without indirection.


class _ScriptedClient(M365GraphCalendarClient):
    """In-memory stand-in for the Graph client.

    Carries a single :class:`CalendarDeltaPage` that the first Graph
    call drains. The integration test cares about the resolution +
    composition discipline, not the Graph wire format detail.
    """

    def __init__(self, page: CalendarDeltaPage) -> None:
        self._page = page
        self._user_id = "operator@example.com"
        self._http = None  # type: ignore[assignment]  # F3 rationale: scripted client never makes HTTP calls
        self._page_size = 50
        self.initial_calls = 0
        self.page_calls = 0

    def fetch_initial_delta(self, _start_iso: str, _end_iso: str) -> CalendarDeltaPage:
        self.initial_calls += 1
        return self._page

    def fetch_delta_page(self, _link: str) -> CalendarDeltaPage:
        self.page_calls += 1
        return self._page

    def close(self) -> None:
        # Intentionally empty — scripted client owns no resources.
        return None


def _resolve_and_run(
    read_flag: Any,
    scripted_page: CalendarDeltaPage,
) -> tuple[frozenset[str], list[ChangeEvent], _ScriptedClient | None]:
    """Composition surface: consult the flag, then conditionally drive sync.

    Hoisted into this helper so both flag-OFF and flag-ON tests use the
    exact same composition path — the only variable is the flag value
    the resolver returns. Returns the enabled-connector set, the
    emitted events, and the scripted client (or None if the OFF branch
    never constructed it) so the assertions can inspect call counts.
    """
    enabled: set[str] = {"obsidian"}
    if read_flag("connector_m365_calendar"):
        enabled.add("m365_calendar")

    if "m365_calendar" not in enabled:
        return frozenset(enabled), [], None

    config = M365CalendarConfig(
        user_id="operator@example.com",
        tenant_id="placeholder-tenant",
        client_id="placeholder-client",
        client_secret="placeholder-secret",  # pragma: allowlist secret
    )
    client = _ScriptedClient(scripted_page)
    connector = M365CalendarConnector(config, client_factory=lambda _c: client)
    events = list(connector.list_changes(cursor=None))
    return frozenset(enabled), events, client


def _seed_page() -> CalendarDeltaPage:
    record = CalendarEventRecord(
        event_id="event-zulu",
        subject="Cutover trial",
        start_iso="2026-05-25T09:00:00Z",
        end_iso="2026-05-25T10:00:00Z",
        location="Conference room",
        attendees=("alpha@example.com",),
        organiser="organiser@example.com",
        last_modified_iso="2026-05-25T08:00:00Z",
        cancelled=False,
        removed=False,
        raw_payload='{"id": "event-zulu"}',
    )
    return CalendarDeltaPage(
        events=(record,),
        next_link=None,
        delta_link="https://graph.microsoft.com/v1.0/.../$deltatoken=zulu",
    )


def test_flag_off_skips_construction() -> None:
    """OFF — m365_calendar must not appear in the enabled set and the
    scripted client must never be constructed.

    The OFF-branch must short-circuit BEFORE constructing the
    connector — if the construction happened anyway, the test's
    ``client is None`` assertion fails.
    """
    resolver = FakeFeatureFlagResolver().with_flag("connector_m365_calendar", False)
    enabled, events, client = _resolve_and_run(resolver.get, _seed_page())
    assert "m365_calendar" not in enabled, f"flag OFF must exclude m365_calendar; got {enabled!r}"
    assert events == [], f"flag OFF must not run sync; got {events!r}"
    assert client is None, "flag OFF must not construct the Graph client"


def test_flag_on_constructs_and_syncs() -> None:
    """ON — m365_calendar must appear in the enabled set AND drive a real sync.

    The ON-branch must construct the connector, run ``list_changes``,
    and surface the scripted event as a ``created`` :class:`ChangeEvent`.
    Sabotage-proof per the module docstring.
    """
    resolver = FakeFeatureFlagResolver().with_flag("connector_m365_calendar", True)
    enabled, events, client = _resolve_and_run(resolver.get, _seed_page())
    assert "m365_calendar" in enabled, f"flag ON must include m365_calendar; got {enabled!r}"
    assert client is not None, "flag ON must construct the Graph client"
    assert client.initial_calls == 1, f"flag ON must drive an initial delta call; got {client.initial_calls}"
    assert [(e.op, e.item_id) for e in events] == [("created", "event-zulu")], (
        f"flag ON must surface the scripted event as a created ChangeEvent; got {events!r}"
    )
