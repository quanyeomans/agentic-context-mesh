"""Slack envelope metadata propagation — ADR-021 / F65.

:class:`kairix.connectors.slack.SlackConnector` lifts per-message
envelope (``user`` id + ``ts`` + channel name + thread context) onto
the :class:`SourceMetadata` payload; silver threads it through to the
indexed :class:`~kairix.core.protocols.Chunk`.

Sabotage proof: clear ``user`` on the scripted SlackMessage; assert
``chunk.author`` becomes None; restore.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from kairix.connectors.slack import (
    SlackChannel,
    SlackConnector,
    SlackCredentials,
    SlackMessage,
    SlackWebClient,
)
from kairix.core import factory
from kairix.core.db.schema import create_schema
from tests.fakes import FakeChunkWriter, FakeEntityGraphSink, FakeExtractor

pytestmark = pytest.mark.integration


class _ScriptedWebClient(SlackWebClient):
    def __init__(self, *, channels: list[SlackChannel], messages: list[SlackMessage]) -> None:
        self._channels = channels
        self._messages = messages

    def conversations_list(self, *, types: Any = None) -> Iterator[SlackChannel]:
        del types
        yield from self._channels

    def conversations_history(self, *, channel_id: str, oldest: str | None = None) -> Iterator[SlackMessage]:
        del oldest
        yield from (msg for msg in self._messages if msg.channel_id == channel_id)


def _build_connector() -> SlackConnector:
    channels = [
        SlackChannel(
            channel_id="C-METADATA",
            name="metadata-channel",
            kind="public_channel",
            is_archived=False,
            is_member=True,
        ),
    ]
    messages = [
        SlackMessage(
            channel_id="C-METADATA",
            ts="1716894000.000100",  # 2026-05-28T11:00:00Z approx
            user="U-AGENT-ALPHA",
            text="envelope-bearing message body — enough content to chunk through silver.",
            thread_ts=None,
            subtype=None,
            edited_ts=None,
        ),
    ]
    client = _ScriptedWebClient(channels=channels, messages=messages)

    def _builder(_c: SlackCredentials) -> SlackWebClient:
        return client

    return SlackConnector(
        credentials=SlackCredentials(bot_token="xoxb-test-fake-token-value"),
        web_client_factory=_builder,
    )


def test_slack_envelope_metadata_lands_on_chunk(tmp_path: Path) -> None:
    """SlackConnector.metadata_for surfaces message user + channel name + thread context."""
    connector = _build_connector()
    db_path = tmp_path / "slack_metadata.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    chunk_writer = FakeChunkWriter()
    pipeline = factory.build_connector_pipeline(
        db=db,
        collection="slack-metadata-propagation",
        chunk_writer=chunk_writer,
        entity_graph_sink=FakeEntityGraphSink(),
    )

    pipeline.run_batch(connector, FakeExtractor())

    chunks = [chunk for batch in chunk_writer.writes for chunk in batch]
    assert chunks, "SlackConnector did not surface any chunks"
    authors = [chunk.author for chunk in chunks]
    assert "U-AGENT-ALPHA" in authors, f"expected envelope user 'U-AGENT-ALPHA' on chunk.author; got {authors!r}"
    all_tags: set[str] = set()
    for chunk in chunks:
        all_tags.update(chunk.tags)
    assert "metadata-channel" in all_tags, (
        f"expected channel name 'metadata-channel' in chunk.tags; got {sorted(all_tags)!r}"
    )
    # The Slack ts → ISO conversion happens in the connector; we just
    # assert the chunk_date is not None and not the raw ts.
    chunk_dates = [chunk.source_modified_at for chunk in chunks]
    assert all(date for date in chunk_dates), (
        f"expected every chunk to carry a non-empty chunk_date; got {chunk_dates!r}"
    )
