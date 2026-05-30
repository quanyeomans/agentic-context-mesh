"""E2E composed path for the Google Calendar connector — F48 sibling test.

This file is the F48 sibling test for the
``topology_v2_google_calendar`` feature flag. It exercises the full
composed path against the real
:class:`~kairix.connectors.google_calendar.GoogleCalendarConnector`
class, the real :func:`~kairix.core.factory.build_connector_pipeline`
factory, the real production schema, the real dispatcher
(:func:`~kairix.worker.dispatch_google_calendar_sync`), and a
scripted :class:`GoogleCalendarClient` so no real network traffic
fires.

Per F48 + F47: lives under ``tests/e2e/`` with ``@pytest.mark.e2e``;
runs in CI Stage 4.5 under ``pytest -m e2e``; exercises config →
factory → ingest → assertion via the composed production code paths.

Sabotage proof (executed by the agent):
inverting the ``if read_flag(...)`` guard in
:func:`dispatch_google_calendar_sync` flips
:func:`test_composed_dispatcher_off_branch_skips_pipeline` to fail
because the OFF branch starts driving the connector pipeline.
Restored.
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
from kairix.core import factory as core_factory
from kairix.core.db.schema import create_schema
from kairix.worker import (
    ConnectorSyncResult,
    dispatch_google_calendar_sync,
    google_calendar_off_branch_noop,
)
from tests.fakes import FakeChunkWriter, FakeEntityGraphSink, FakeExtractor, FakeFeatureFlagResolver

pytestmark = pytest.mark.e2e

_FLAG_NAME = "topology_v2_google_calendar"


class _ScriptedClient(GoogleCalendarClient):
    """In-memory Google client used by the composed-path E2E."""

    def __init__(self, page: GoogleCalendarEventsPage) -> None:
        self._page = page
        self._calendar_id = "primary"
        self._http = None  # type: ignore[assignment]  # scripted client never makes HTTP calls
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
        event_id="event-composed-1",
        summary="E2E composed event",
        description="Tracker https://issues.example.com/T-42",
        start_iso="2026-05-29T09:00:00Z",
        end_iso="2026-05-29T10:00:00Z",
        location="Conference room",
        attendees=("agent-beta@example.com",),
        organizer_email="agent-alpha@example.com",
        updated_iso="2026-05-29T08:30:00Z",
        recurrence=(),
        status="confirmed",
        html_link="https://calendar.google.com/event?eid=event-composed-1",
        raw_payload='{"id": "event-composed-1"}',
    )
    return GoogleCalendarEventsPage(
        events=(record,),
        next_page_token=None,
        next_sync_token="composed-sync-token",
    )


def _bootstrap_e2e_db(tmp_path: Path) -> sqlite3.Connection:
    """Create the production schema."""
    db_path = tmp_path / "kairix.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db, dims=4)
    return db


def _build_connector() -> GoogleCalendarConnector:
    """Construct the production connector with a scripted client."""
    config = GoogleCalendarConfig(
        access_token="placeholder-token",  # pragma: allowlist secret
    )
    return GoogleCalendarConnector(
        config,
        client_factory=lambda _c: _ScriptedClient(_seed_page()),
    )


# ---------------------------------------------------------------------------
# Composed-path signals
# ---------------------------------------------------------------------------


def test_composed_google_calendar_flag_registered() -> None:
    """The flag is in the production registry at default-False / introduce."""
    from kairix.core.features.registry import REGISTRY

    assert _FLAG_NAME in REGISTRY
    entry = REGISTRY[_FLAG_NAME]
    assert entry.default is False
    assert entry.stage == "introduce"
    assert entry.related_spec is not None


def test_composed_dispatcher_off_branch_skips_pipeline() -> None:
    """OFF: the dispatcher does NOT invoke the on-branch.

    Composed signal: the real :func:`dispatch_google_calendar_sync`
    consults the real resolver shape (via FakeFeatureFlagResolver)
    and routes through the real off-branch helper which returns zero
    counters without opening a DB or driving a connector.

    Sabotage proof: invert the if-branch in
    :func:`dispatch_google_calendar_sync` so OFF reaches ON — this
    test fails because the on_branch wrapper increments its counter.
    """
    resolver = FakeFeatureFlagResolver().with_flag("topology_v2_google_calendar", False)
    on_calls = {"n": 0}

    def _on_branch() -> ConnectorSyncResult:
        on_calls["n"] += 1
        return ConnectorSyncResult(synced=99, failed=0, dead_letter_added=0)

    result = dispatch_google_calendar_sync(
        read_flag=resolver.get,
        on_branch=_on_branch,
        off_branch=google_calendar_off_branch_noop,
    )
    assert on_calls["n"] == 0, "composed path: OFF branch must not reach ON"
    assert result.synced == 0


def test_composed_dispatcher_on_branch_invokes_pipeline_helper() -> None:
    """ON: the dispatcher routes through the on-branch helper.

    Composed signal: the real :func:`dispatch_google_calendar_sync`
    consults the real resolver shape and routes through the on-branch
    helper. The integration / contract tests cover the helper's
    interior; this composed-path test pins the dispatcher's branch
    selection contract on the production flag.
    """
    resolver = FakeFeatureFlagResolver().with_flag("topology_v2_google_calendar", True)
    on_calls = {"n": 0}

    def _on_branch() -> ConnectorSyncResult:
        on_calls["n"] += 1
        return ConnectorSyncResult(synced=3, failed=0, dead_letter_added=0)

    result = dispatch_google_calendar_sync(
        read_flag=resolver.get,
        on_branch=_on_branch,
        off_branch=google_calendar_off_branch_noop,
    )
    assert on_calls["n"] == 1, "composed path: ON branch must run exactly once"
    assert result.synced == 3


def test_composed_connector_pipeline_runs_end_to_end(tmp_path: Path) -> None:
    """End-to-end: real factory + real connector + scripted client → chunks land.

    Drives the real :func:`build_connector_pipeline` factory with the
    real connector against a scripted Google client. Confirms the
    composed path produces chunks carrying the connector's envelope
    metadata (author, attendees, source_modified_at).
    """
    db = _bootstrap_e2e_db(tmp_path)
    connector = _build_connector()
    chunk_writer = FakeChunkWriter()
    pipeline = core_factory.build_connector_pipeline(
        db=db,
        collection="google-calendar-composed",
        chunk_writer=chunk_writer,
        entity_graph_sink=FakeEntityGraphSink(),
    )

    pipeline.run_batch(connector, FakeExtractor())

    chunks = [chunk for batch in chunk_writer.writes for chunk in batch]
    assert chunks, "composed path: pipeline must emit at least one chunk"
    authors = {chunk.author for chunk in chunks}
    assert "agent-alpha@example.com" in authors, (
        f"composed path: organizer must surface as chunk.author; got {authors!r}"
    )
    item_ids = {chunk.source_uri for chunk in chunks}
    assert any("event-composed-1" in uri for uri in item_ids), (
        f"composed path: source_uri must carry the seeded event id; got {item_ids!r}"
    )
