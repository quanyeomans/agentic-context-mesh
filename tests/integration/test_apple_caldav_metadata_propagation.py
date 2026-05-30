"""Apple CalDAV envelope metadata propagation — ADR-021 / F65.

:class:`kairix.connectors.apple_caldav.AppleCalDavConnector` lifts the
CalDAV event envelope (ORGANIZER + LAST-MODIFIED + ATTENDEE + SUMMARY
+ DTSTART + DTEND + RRULE + LOCATION) onto the :class:`SourceMetadata`
payload; silver threads it through to the indexed
:class:`~kairix.core.protocols.Chunk`.

Sabotage proof: mutate the connector to drop the ``recurrence_rule``
extraction in ``metadata_for`` (return ``properties`` without it);
assert ``chunk.metadata["recurrence_rule"]`` becomes absent; restore.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kairix.connectors.apple_caldav import (
    AppleCalDavClient,
    AppleCalDavConfig,
    AppleCalDavConnector,
    CalDavCalendarRef,
    CalendarEventRecord,
    CalendarSyncPage,
)
from kairix.core import factory
from kairix.core.db.schema import create_schema
from tests.fakes import FakeChunkWriter, FakeEntityGraphSink, FakeExtractor

pytestmark = pytest.mark.integration

_FIXTURE_CALENDAR_URL = "https://p01-caldav.icloud.com/12345/calendars/personal/"


class _ScriptedClient(AppleCalDavClient):
    """In-memory CalDAV client — no real iCloud or HTTP traffic."""

    def __init__(self, page: CalendarSyncPage) -> None:
        # Skip the real __init__ — no auth, no caldav library import.
        self._username = "agent-alpha@example.com"
        self._password = "fixture-app-password"  # pragma: allowlist secret — test fixture
        self._endpoint = "https://caldav.icloud.com"
        self._dav_client_factory = None
        self._dav_client = None
        self._page = page

    def discover_calendars(self) -> tuple[CalDavCalendarRef, ...]:
        return (
            CalDavCalendarRef(
                url=_FIXTURE_CALENDAR_URL,
                display_name="Personal",
                ctag="ctag-1",
            ),
        )

    def list_changes(self, calendar_url: str, sync_token: str | None) -> CalendarSyncPage:
        del calendar_url, sync_token
        return self._page

    def fetch(self, event_url: str) -> CalendarEventRecord:
        del event_url
        return self._page.events[0]


def _seed_page() -> CalendarSyncPage:
    record = CalendarEventRecord(
        event_id="event-metadata-1",
        summary="Envelope-bearing meeting",
        dtstart_iso="2026-05-28T09:00:00Z",
        dtend_iso="2026-05-28T10:00:00Z",
        location="Conference room",
        attendees=("attendee@example.com",),
        organiser="agent-alpha@example.com",
        last_modified_iso="2026-05-28T08:30:00Z",
        recurrence_rule="FREQ=WEEKLY;COUNT=4",
        cancelled=False,
        removed=False,
        raw_ics=(
            "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:event-metadata-1\n"
            "SUMMARY:Envelope-bearing meeting\nEND:VEVENT\nEND:VCALENDAR\n"
        ),
        event_url=_FIXTURE_CALENDAR_URL + "event-metadata-1.ics",
    )
    return CalendarSyncPage(events=(record,), sync_token="metadata-sync-token")


def test_apple_caldav_envelope_metadata_lands_on_chunk(tmp_path: Path) -> None:
    """AppleCalDavConnector.metadata_for surfaces ORGANIZER + LAST-MODIFIED + ATTENDEE + RRULE."""
    config = AppleCalDavConfig(
        username="agent-alpha@example.com",
        password="fixture-app-password",  # pragma: allowlist secret — test fixture
    )
    scripted_client = _ScriptedClient(_seed_page())
    connector = AppleCalDavConnector(config, client_factory=lambda _c: scripted_client)
    db_path = tmp_path / "apple_caldav_metadata.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    chunk_writer = FakeChunkWriter()
    pipeline = factory.build_connector_pipeline(
        db=db,
        collection="apple-caldav-metadata-propagation",
        chunk_writer=chunk_writer,
        entity_graph_sink=FakeEntityGraphSink(),
    )

    pipeline.run_batch(connector, FakeExtractor())

    chunks = [chunk for batch in chunk_writer.writes for chunk in batch]
    assert chunks, "AppleCalDavConnector did not surface any chunks"
    authors = [chunk.author for chunk in chunks]
    assert "agent-alpha@example.com" in authors, (
        f"expected envelope ORGANIZER 'agent-alpha@example.com' on chunk.author; got {authors!r}"
    )
    chunk_dates = [chunk.source_modified_at for chunk in chunks]
    assert "2026-05-28T08:30:00Z" in chunk_dates, (
        f"expected envelope LAST-MODIFIED on chunk.source_modified_at; got {chunk_dates!r}"
    )
    all_tags: set[str] = set()
    for chunk in chunks:
        all_tags.update(chunk.tags)
    assert "attendee@example.com" in all_tags, f"expected attendee in chunk.tags; got {sorted(all_tags)!r}"
    # RRULE propagates through chunk.metadata (this is the sabotage-proof target).
    all_rrules: set[str] = set()
    for chunk in chunks:
        rrule = chunk.metadata.get("recurrence_rule") if hasattr(chunk.metadata, "get") else None
        if rrule:
            all_rrules.add(str(rrule))
    assert "FREQ=WEEKLY;COUNT=4" in all_rrules, (
        f"expected RRULE 'FREQ=WEEKLY;COUNT=4' on chunk.metadata['recurrence_rule']; got {all_rrules!r}"
    )
