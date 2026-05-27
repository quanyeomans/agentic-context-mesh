"""Notion envelope metadata propagation — ADR-021 / F65.

:class:`kairix.connectors.notion.NotionConnector` lifts the page
envelope (``last_edited_by`` / ``last_edited_time`` /
``created_time`` / ``parent.type``) onto the
:class:`SourceMetadata` payload; silver threads it through to the
indexed :class:`~kairix.core.protocols.Chunk`.

Sabotage proof: clear ``last_edited_by.id`` on the scripted page
payload; assert ``chunk.author`` becomes None; restore.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import httpx
import pytest

from kairix.connectors.notion import (
    NotionApiClient,
    NotionConnector,
    NotionCredentials,
)
from kairix.core import factory
from kairix.core.db.schema import create_schema
from tests.fakes import FakeChunkWriter, FakeEntityGraphSink, FakeExtractor

pytestmark = pytest.mark.integration


def _payload() -> dict[str, Any]:
    return {
        "results": [
            {
                "object": "page",
                "id": "page-metadata-1",
                "url": "https://www.notion.so/your-team/page-metadata-1",
                "last_edited_time": "2026-05-28T10:00:00.000Z",
                "created_time": "2026-05-20T08:00:00.000Z",
                "archived": False,
                "parent": {"type": "workspace", "workspace": True},
                "created_by": {"object": "user", "id": "user-agent-alpha"},
                "last_edited_by": {"object": "user", "id": "user-agent-alpha"},
                "properties": {
                    "title": {
                        "type": "title",
                        "title": [{"type": "text", "plain_text": "Envelope-bearing page"}],
                    }
                },
            }
        ],
        "next_cursor": None,
        "has_more": False,
    }


def _build_connector() -> NotionConnector:
    def _stub(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        # Block fetches return a simple block list so render_page_markdown
        # has at least one paragraph of content to chunk.
        if "/blocks/" in url and "/children" in url:
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "object": "block",
                            "id": "block-1",
                            "type": "paragraph",
                            "has_children": False,
                            "paragraph": {"rich_text": [{"plain_text": "body paragraph content"}]},
                        }
                    ],
                    "next_cursor": None,
                    "has_more": False,
                },
            )
        return httpx.Response(200, json=_payload())

    shared = httpx.Client(transport=httpx.MockTransport(_stub))
    return NotionConnector(
        credentials=NotionCredentials(token="secret_fake_token_value"),  # pragma: allowlist secret — test fixture
        client_builder=lambda creds: NotionApiClient(token=creds.token, http_client=shared),
    )


def test_notion_envelope_metadata_lands_on_chunk(tmp_path: Path) -> None:
    """NotionConnector.metadata_for surfaces last_edited_by + last_edited_time + parent_type tag."""
    connector = _build_connector()
    db_path = tmp_path / "notion_metadata.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    chunk_writer = FakeChunkWriter()
    pipeline = factory.build_connector_pipeline(
        db=db,
        collection="notion-metadata-propagation",
        chunk_writer=chunk_writer,
        entity_graph_sink=FakeEntityGraphSink(),
    )

    pipeline.run_batch(connector, FakeExtractor())

    chunks = [chunk for batch in chunk_writer.writes for chunk in batch]
    assert chunks, "NotionConnector did not surface any chunks"
    authors = [chunk.author for chunk in chunks]
    assert "user-agent-alpha" in authors, (
        f"expected envelope last_edited_by id 'user-agent-alpha' on chunk.author; got {authors!r}"
    )
    chunk_dates = [chunk.source_modified_at for chunk in chunks]
    assert "2026-05-28T10:00:00.000Z" in chunk_dates, (
        f"expected envelope last_edited_time on chunk_date; got {chunk_dates!r}"
    )
    all_tags: set[str] = set()
    for chunk in chunks:
        all_tags.update(chunk.tags)
    assert "workspace" in all_tags, f"expected envelope parent_type 'workspace' in chunk.tags; got {sorted(all_tags)!r}"
