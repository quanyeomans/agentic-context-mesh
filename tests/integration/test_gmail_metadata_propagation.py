"""Gmail envelope metadata propagation — ADR-021 / F65.

:class:`kairix.connectors.gmail.GmailConnector` exposes the Subject /
From / To / Cc / Date / Thread / Labels per Gmail envelope;
``metadata_for`` lifts those onto :class:`SourceMetadata` and Silver
threads it through to the indexed :class:`Chunk`.

The canonical sabotage proof for this test:

  * Drop the ``From`` header parse in :func:`_headers_by_name` (e.g.
    by mutating it to return ``{}``). The connector's metadata_for
    returns an envelope with ``author=None``; the chunk write loses
    the author signal; this test fails. Restore returns to green.

F47 — constructs the multi-component pipeline via
:func:`kairix.core.factory.build_connector_pipeline` with FakeChunkWriter
+ FakeEntityGraphSink + FakeExtractor (the canonical fakes from
``tests/fakes.py``), not by direct ``ConnectorPipeline(...)``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from kairix.connectors.gmail import GmailConnector
from kairix.connectors.gmail.client import (
    GmailHeader,
    GmailMessage,
    HistoryPage,
)
from kairix.core import factory
from kairix.core.db.schema import create_schema
from tests.fakes import FakeChunkWriter, FakeEntityGraphSink, FakeExtractor

pytestmark = pytest.mark.integration


class _ScriptedClient:
    """Scripted GmailClient that emits one message with full envelope."""

    def __init__(self) -> None:
        self._last_history_id: str | None = None

    def get_profile_history_id(self) -> str:
        return "tip-cold-start"

    def list_history(self, *, start_history_id: str, page_token: str | None = None) -> HistoryPage:
        _ = (start_history_id, page_token)
        return HistoryPage(
            message_ids=("gmail-metadata-msg-1",),
            next_page_token=None,
            history_id="tip-after-drain",
        )

    def iter_history_message_ids(self, *, start_history_id: str) -> Any:
        page = self.list_history(start_history_id=start_history_id)
        self._last_history_id = page.history_id
        yield from page.message_ids

    def last_history_id(self) -> str | None:
        return self._last_history_id

    def get_message(self, message_id: str) -> GmailMessage:
        return GmailMessage(
            message_id=message_id,
            thread_id="thread-metadata-1",
            history_id="1234",
            label_ids=("INBOX", "IMPORTANT"),
            headers=(
                GmailHeader(name="Subject", value="Envelope-bearing email"),
                GmailHeader(name="From", value="agent-alpha@example.com"),
                GmailHeader(name="To", value="recipient@example.com"),
                GmailHeader(name="Cc", value="cc-recipient@example.com"),
                GmailHeader(name="Date", value="2026-05-28T07:00:01Z"),
            ),
            body=b"Body of the envelope-bearing email.",
            body_mime="text/plain",
            body_truncated=False,
            attachments=(),
        )

    def stats(self) -> Any:
        from kairix.connectors.gmail.client import GmailStatsSnapshot

        return GmailStatsSnapshot(requests=0, rate_limited_403_total=0, token_refreshes=0)

    def invalidate_token(self) -> None:
        return None


def test_gmail_envelope_metadata_lands_on_chunk(tmp_path: Path) -> None:
    """GmailConnector.metadata_for surfaces From + Date + To onto the chunk.

    Sabotage proof: dropping the From header parse in metadata_for
    (return ``properties=...`` without ``author=...``) makes the
    assertion ``"agent-alpha@example.com" in authors`` fail.
    """
    client = _ScriptedClient()
    connector = GmailConnector(user_email="agent-alpha@example.com", client=client)  # type: ignore[arg-type]  # F3 rationale: scripted client stand-in.
    db_path = tmp_path / "gmail_metadata.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    chunk_writer = FakeChunkWriter()
    pipeline = factory.build_connector_pipeline(
        db=db,
        collection="gmail-metadata-propagation",
        chunk_writer=chunk_writer,
        entity_graph_sink=FakeEntityGraphSink(),
    )

    # Cold-start: seeds the cursor at the live tip with no events.
    pipeline.run_batch(connector, FakeExtractor())
    # Warm tick: drains the scripted history page into one chunk.
    pipeline.run_batch(connector, FakeExtractor())

    chunks = [chunk for batch in chunk_writer.writes for chunk in batch]
    assert chunks, "GmailConnector did not surface any chunks"
    authors = [chunk.author for chunk in chunks]
    assert "agent-alpha@example.com" in authors, (
        f"expected envelope From 'agent-alpha@example.com' on chunk.author; got {authors!r}"
    )
    chunk_dates = [chunk.source_modified_at for chunk in chunks]
    assert "2026-05-28T07:00:01Z" in chunk_dates, f"expected envelope Date on chunk_date; got {chunk_dates!r}"
    all_tags: set[str] = set()
    for chunk in chunks:
        all_tags.update(chunk.tags)
    assert "recipient@example.com" in all_tags, f"expected To recipient in chunk.tags; got {sorted(all_tags)!r}"
    all_properties: dict[str, str] = {}
    for chunk in chunks:
        all_properties.update(chunk.metadata)
    assert all_properties.get("subject") == "Envelope-bearing email"
    assert all_properties.get("thread_id") == "thread-metadata-1"
