"""Google Calendar envelope metadata propagation — ADR-021 / F65.

:class:`kairix.connectors.google_calendar.GoogleCalendarConnector`
lifts the Google event envelope (organizer + updated + attendees +
location + recurrence + linked-doc URLs from the description) onto
the :class:`SourceMetadata` payload; silver threads it through to the
indexed :class:`~kairix.core.protocols.Chunk`.

Sabotage proof: clear ``organizer_email`` on the scripted record;
assert ``chunk.author`` becomes None; restore.

Sabotage proof: drop the ``attendees`` field on the scripted record;
assert ``chunk.tags`` is empty; restore.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kairix.connectors.google_calendar import (
    GoogleCalendarConfig,
    GoogleCalendarConnector,
)
from kairix.connectors.google_calendar.client import (
    GoogleCalendarClient,
    GoogleCalendarEventRecord,
    GoogleCalendarEventsPage,
)
from kairix.core import factory
from kairix.core.db.schema import create_schema
from tests.fakes import FakeChunkWriter, FakeEntityGraphSink, FakeExtractor

pytestmark = pytest.mark.integration

_ORGANIZER = "agent-alpha@example.com"
_ATTENDEE = "agent-beta@example.com"
_LINKED_DOC = "https://docs.example.com/standup-agenda"


class _ScriptedClient(GoogleCalendarClient):
    """In-memory Google client used by the integration test."""

    def __init__(self, page: GoogleCalendarEventsPage) -> None:
        self._page = page
        self._calendar_id = "primary"
        self._http = None  # type: ignore[assignment]  # F3 rationale: scripted client owns no HTTP — boundary-only suppression.
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


def _seed_page() -> GoogleCalendarEventsPage:
    record = GoogleCalendarEventRecord(
        event_id="event-metadata-1",
        summary="Envelope-bearing standup",
        description=f"Agenda lives at {_LINKED_DOC} and tracker at https://issues.example.com/T-9",
        start_iso="2026-05-28T09:00:00Z",
        end_iso="2026-05-28T09:30:00Z",
        location="Conference room",
        attendees=(_ATTENDEE,),
        organizer_email=_ORGANIZER,
        updated_iso="2026-05-28T08:30:00Z",
        recurrence=("RRULE:FREQ=WEEKLY;BYDAY=MO",),
        status="confirmed",
        html_link="https://calendar.google.com/event?eid=event-metadata-1",
        raw_payload='{"id": "event-metadata-1"}',
    )
    return GoogleCalendarEventsPage(
        events=(record,),
        next_page_token=None,
        next_sync_token="metadata-tok-next",
    )


def test_google_calendar_envelope_metadata_lands_on_chunk(tmp_path: Path) -> None:
    """GoogleCalendarConnector.metadata_for surfaces organizer + updated + attendees + linked-docs."""
    config = GoogleCalendarConfig(
        access_token="fake-access-token",  # pragma: allowlist secret — test fixture
    )
    scripted_client = _ScriptedClient(_seed_page())
    connector = GoogleCalendarConnector(config, client_factory=lambda _c: scripted_client)
    db_path = tmp_path / "google_calendar_metadata.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    chunk_writer = FakeChunkWriter()
    pipeline = factory.build_connector_pipeline(
        db=db,
        collection="google-calendar-metadata-propagation",
        chunk_writer=chunk_writer,
        entity_graph_sink=FakeEntityGraphSink(),
    )

    pipeline.run_batch(connector, FakeExtractor())

    chunks = [chunk for batch in chunk_writer.writes for chunk in batch]
    assert chunks, "GoogleCalendarConnector did not surface any chunks"

    authors = [chunk.author for chunk in chunks]
    assert _ORGANIZER in authors, f"expected envelope organizer {_ORGANIZER!r} on chunk.author; got {authors!r}"

    author_emails = [chunk.author_email for chunk in chunks]
    assert _ORGANIZER in author_emails, (
        f"expected envelope organizer email {_ORGANIZER!r} on chunk.author_email; got {author_emails!r}"
    )

    chunk_dates = [chunk.source_modified_at for chunk in chunks]
    assert "2026-05-28T08:30:00Z" in chunk_dates, (
        f"expected envelope 'updated' on chunk.source_modified_at; got {chunk_dates!r}"
    )

    all_tags: set[str] = set()
    for chunk in chunks:
        all_tags.update(chunk.tags)
    assert _ATTENDEE in all_tags, f"expected attendee in chunk.tags; got {sorted(all_tags)!r}"

    # ADR-021 metadata bag — linked_docs regex-extracted from description.
    all_metadata: dict[str, str] = {}
    for chunk in chunks:
        for k, v in chunk.metadata.items():
            all_metadata[k] = v
    linked = all_metadata.get("linked_docs", "")
    assert _LINKED_DOC in linked, (
        f"expected description URL {_LINKED_DOC!r} in chunk.metadata.linked_docs; got {linked!r}"
    )

    # Recurrence master surfaces as a single chunk with the RRULE captured
    # in metadata — NOT N per-occurrence chunks (ADR-028).
    recurrence = all_metadata.get("recurrence_rule", "")
    assert "RRULE:FREQ=WEEKLY" in recurrence, (
        f"expected RRULE captured in chunk.metadata.recurrence_rule; got {recurrence!r}"
    )

    # calendar_id is always present on properties so downstream filters
    # can scope per-calendar.
    assert all_metadata.get("calendar_id") == "primary", f"expected calendar_id in chunk.metadata; got {all_metadata!r}"
