"""M365 calendar envelope metadata propagation — ADR-021 / F65.

:class:`kairix.connectors.m365_calendar.M365CalendarConnector` lifts
the Graph event envelope (organiser + lastModifiedDateTime + attendees
+ subject + start) onto the :class:`SourceMetadata` payload; silver
threads it through to the indexed
:class:`~kairix.core.protocols.Chunk`.

Sabotage proof: clear ``organiser`` on the scripted CalendarEventRecord;
assert ``chunk.author`` becomes None; restore.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kairix.connectors.m365_calendar import (
    M365CalendarConfig,
    M365CalendarConnector,
)
from kairix.connectors.m365_calendar.graph_client import (
    CalendarDeltaPage,
    CalendarEventRecord,
    M365GraphCalendarClient,
)
from kairix.core import factory
from kairix.core.db.schema import create_schema
from tests.fakes import FakeChunkWriter, FakeEntityGraphSink, FakeExtractor

pytestmark = pytest.mark.integration


class _ScriptedClient(M365GraphCalendarClient):
    def __init__(self, page: CalendarDeltaPage) -> None:
        self._page = page
        self._user_id = "agent-alpha@example.com"
        self._http = None  # type: ignore[assignment]  # F3 rationale: scripted client owns no HTTP — boundary-only suppression.
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
        event_id="event-metadata-1",
        subject="Envelope-bearing meeting",
        start_iso="2026-05-28T09:00:00Z",
        end_iso="2026-05-28T10:00:00Z",
        location="Conference room",
        attendees=("attendee@example.com",),
        organiser="agent-alpha@example.com",
        last_modified_iso="2026-05-28T08:30:00Z",
        cancelled=False,
        removed=False,
        raw_payload='{"id": "event-metadata-1"}',
    )
    return CalendarDeltaPage(
        events=(record,),
        next_link=None,
        delta_link="https://graph.microsoft.com/v1.0/.../$deltatoken=metadata-tok",
    )


def test_m365_calendar_envelope_metadata_lands_on_chunk(tmp_path: Path) -> None:
    """M365CalendarConnector.metadata_for surfaces organiser + lastModifiedDateTime + attendees."""
    config = M365CalendarConfig(
        user_id="agent-alpha@example.com",
        tenant_id="fake-tenant",
        client_id="fake-client",
        client_secret="fake-secret-value",  # pragma: allowlist secret — test fixture
    )
    scripted_client = _ScriptedClient(_seed_page())
    connector = M365CalendarConnector(config, client_factory=lambda _c: scripted_client)
    db_path = tmp_path / "m365_calendar_metadata.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    chunk_writer = FakeChunkWriter()
    pipeline = factory.build_connector_pipeline(
        db=db,
        collection="m365-calendar-metadata-propagation",
        chunk_writer=chunk_writer,
        entity_graph_sink=FakeEntityGraphSink(),
    )

    pipeline.run_batch(connector, FakeExtractor())

    chunks = [chunk for batch in chunk_writer.writes for chunk in batch]
    assert chunks, "M365CalendarConnector did not surface any chunks"
    authors = [chunk.author for chunk in chunks]
    assert "agent-alpha@example.com" in authors, (
        f"expected envelope organiser 'agent-alpha@example.com' on chunk.author; got {authors!r}"
    )
    chunk_dates = [chunk.source_modified_at for chunk in chunks]
    assert "2026-05-28T08:30:00Z" in chunk_dates, (
        f"expected envelope lastModifiedDateTime on chunk_date; got {chunk_dates!r}"
    )
    all_tags: set[str] = set()
    for chunk in chunks:
        all_tags.update(chunk.tags)
    assert "attendee@example.com" in all_tags, f"expected attendee in chunk.tags; got {sorted(all_tags)!r}"
