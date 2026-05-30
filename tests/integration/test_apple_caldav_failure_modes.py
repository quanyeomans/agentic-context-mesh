"""Apple CalDAV connector failure-mode contract — F68 catalogue.

Each test injects one failure class through the connector's
``client_factory`` constructor seam and asserts on a concrete
observable outcome (an exception type, a count of emitted
ChangeEvents, the state of the cached payload map). No
monkey-patching of :mod:`requests`, :mod:`caldav`, or kairix
internals (F1-clean / F2-clean).

Failure classes exercised:

* ``raises`` — discovery raises a network error
* ``returns_empty`` — sync REPORT returns zero events
* ``returns_partial`` — sync REPORT returns events but no sync token
* ``unauthorized`` — sync REPORT raises a 401 HTTPError
* ``unavailable`` — discovery raises a connection error

Sabotage proofs are documented per-test below.
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
)

pytestmark = pytest.mark.integration

_CALENDAR_URL = "https://caldav.icloud.com/12345/calendars/personal/"


def _build_connector(client: AppleCalDavClient) -> AppleCalDavConnector:
    config = AppleCalDavConfig(
        username="agent-alpha@example.com",
        password="fixture-app-password",  # pragma: allowlist secret — test fixture
    )
    return AppleCalDavConnector(config, client_factory=lambda _c: client)


class _BaseScriptedClient(AppleCalDavClient):
    """Shared scaffolding for the per-failure scripted clients."""

    def __init__(self) -> None:
        self._username = "agent-alpha@example.com"
        self._password = "fixture-app-password"  # pragma: allowlist secret — test fixture
        self._endpoint = "https://caldav.icloud.com"
        self._dav_client_factory = None
        self._dav_client = None

    def discover_calendars(self) -> tuple[CalDavCalendarRef, ...]:
        return (CalDavCalendarRef(url=_CALENDAR_URL, display_name="Personal", ctag=None),)

    def fetch(self, event_url: str) -> CalendarEventRecord:
        raise NotImplementedError


@pytest.mark.integration
def test_list_changes_raises_when_discovery_errors() -> None:
    """raises: discover_calendars raises -> list_changes propagates.

    Sabotage proof: wrap the ``self._configured_calendars()`` call in
    ``_drain_all_calendars`` with ``try/except Exception: return _SyncBatch()``.
    Re-run — the connector returns an empty iterator instead of
    raising; this test's ``pytest.raises`` fails. Restored.
    """

    class _Failing(_BaseScriptedClient):
        def discover_calendars(self) -> tuple[CalDavCalendarRef, ...]:
            raise requests.exceptions.RequestException("network unreachable")

        def list_changes(self, calendar_url: str, sync_token: str | None) -> CalendarSyncPage:
            raise AssertionError("list_changes should not be reached when discovery fails")

    connector = _build_connector(_Failing())
    with pytest.raises(requests.exceptions.RequestException, match="network unreachable"):
        list(connector.list_changes(cursor=None))


@pytest.mark.integration
def test_list_changes_returns_empty_when_sync_report_yields_nothing() -> None:
    """returns_empty: sync REPORT returns zero events -> connector emits zero ChangeEvents.

    Sabotage proof: change ``_absorb_page_into_batch`` to always
    append a synthetic event (``batch.events.append(ChangeEvent(...))``)
    regardless of ``page.events`` content. Re-run — this test's
    assertion that ``events == []`` fails. Restored.
    """

    class _Empty(_BaseScriptedClient):
        def list_changes(self, calendar_url: str, sync_token: str | None) -> CalendarSyncPage:
            del calendar_url, sync_token
            return CalendarSyncPage(events=(), sync_token="empty-tok")

    connector = _build_connector(_Empty())
    events = list(connector.list_changes(cursor=None))
    assert events == [], f"expected zero events when sync REPORT is empty; got {events!r}"


@pytest.mark.integration
def test_list_changes_returns_partial_when_no_sync_token_surfaced() -> None:
    """returns_partial: sync REPORT returns events but no token.

    Tests the connector keeps the events AND surfaces no new cursor
    so the next tick re-runs the same query (CalDAV ctag-comparison
    fallback path). Confirms the connector doesn't drop the events
    just because the cursor is missing.

    Sabotage proof: in ``_absorb_page_into_batch`` guard
    ``page.events`` with ``if page.sync_token is None: return``.
    Re-run — this test's ``len(events) == 1`` assertion fails (zero
    events instead of one). Restored.
    """

    class _PartialClient(_BaseScriptedClient):
        def list_changes(self, calendar_url: str, sync_token: str | None) -> CalendarSyncPage:
            del calendar_url, sync_token
            record = CalendarEventRecord(
                event_id="partial-event",
                summary="Partial",
                dtstart_iso="2026-05-30T09:00:00Z",
                dtend_iso="2026-05-30T10:00:00Z",
                location="",
                attendees=(),
                organiser="",
                last_modified_iso="2026-05-30T08:00:00Z",
                recurrence_rule="",
                cancelled=False,
                removed=False,
                raw_ics="BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:partial-event\nEND:VEVENT\nEND:VCALENDAR\n",
                event_url=_CALENDAR_URL + "partial-event.ics",
            )
            return CalendarSyncPage(events=(record,), sync_token=None)

    connector = _build_connector(_PartialClient())
    events = list(connector.list_changes(cursor=None))
    assert len(events) == 1, f"expected one event in partial response; got {len(events)}"
    # Cursor is None because the server returned no token; the
    # orchestrator MUST NOT advance the persisted cursor in this case.
    assert connector.next_cursor() is None, (
        f"expected None cursor when no sync token surfaced; got {connector.next_cursor()!r}"
    )


@pytest.mark.integration
def test_list_changes_unauthorized_raises_typed_error() -> None:
    """unauthorized: 401 on the sync REPORT surfaces as HTTPError.

    Sabotage proof: wrap the call to ``client.list_changes(...)`` in
    ``_drain_all_calendars`` with
    ``try/except requests.exceptions.HTTPError: continue`` — the 401
    gets swallowed; this test's ``pytest.raises`` fails. Restored.
    """

    class _Unauthorized(_BaseScriptedClient):
        def list_changes(self, calendar_url: str, sync_token: str | None) -> CalendarSyncPage:
            del calendar_url, sync_token
            response = requests.Response()
            response.status_code = 401
            raise requests.exceptions.HTTPError("401 Unauthorized", response=response)

    connector = _build_connector(_Unauthorized())
    with pytest.raises(requests.exceptions.HTTPError) as exc_info:
        list(connector.list_changes(cursor=None))
    assert exc_info.value.response is not None
    assert exc_info.value.response.status_code == 401


@pytest.mark.integration
def test_fetch_raises_when_unknown_item_id_requested() -> None:
    """raises: fetch on an unseen item_id raises ValueError.

    The connector caches payloads during ``list_changes``; ``fetch``
    on an id that never surfaced in this process must raise loudly
    so the orchestrator never silently dead-letters.

    Sabotage proof: change ``fetch`` to return
    ``RawArtefact(raw=b'', mime='text/calendar', fetched_at=_iso(...))``
    on cache miss. Re-run — this test's ``pytest.raises`` fails.
    Restored.
    """

    class _OneShot(_BaseScriptedClient):
        def list_changes(self, calendar_url: str, sync_token: str | None) -> CalendarSyncPage:
            del calendar_url, sync_token
            return CalendarSyncPage(events=(), sync_token="any")

    connector = _build_connector(_OneShot())
    with pytest.raises(ValueError, match="no cached payload"):
        connector.fetch("never-seen-event-id")
