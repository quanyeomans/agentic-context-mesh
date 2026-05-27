"""Dex CRM envelope metadata propagation — ADR-021 / F65.

:class:`kairix.connectors.dex_crm.DexCrmConnector` lifts the per-record
``modified_by`` / ``updated_at`` / ``tags`` envelope into
:class:`SourceMetadata` so the silver-merge layer can thread them onto
the indexed :class:`~kairix.core.protocols.Chunk`.

Sabotage proof: drop ``modified_by`` from the scripted Dex API
response; re-run the test; assert ``chunk.author`` becomes None;
restore.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
import pytest

from kairix.connectors.dex_crm import DexCrmConnector
from kairix.connectors.dex_crm.client import DexCrmClient, DexCrmClientConfig
from kairix.core import factory
from kairix.core.db.schema import create_schema
from kairix.transport.auth.api_key import ApiKeyAuth, BearerHeaders, reset_api_key_cache
from tests.fakes import FakeChunkWriter, FakeEntityGraphSink, FakeExtractor

pytestmark = pytest.mark.integration

_SCRIPTED_CONTACT = {
    "id": "c-metadata-1",
    "updated_at": "2026-05-28T09:00:00Z",
    "created_at": "2026-05-20T08:00:00Z",
    "modified_by": "agent-alpha",
    "created_by": "agent-beta",
    "tags": ["design", "intro-call"],
    "first_name": "Test",
    "last_name": "Contact",
}


class _ScriptedAuth(ApiKeyAuth):
    def headers(self, _secret_name: str) -> BearerHeaders:
        return BearerHeaders(mapping={"Authorization": "Bearer metadata-token"})


def _build_connector() -> DexCrmConnector:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/contacts"):
            return httpx.Response(200, json={"data": [_SCRIPTED_CONTACT], "next_cursor": None})
        return httpx.Response(200, json={"data": [], "next_cursor": None})

    transport = httpx.MockTransport(handler)
    reset_api_key_cache()
    inner_client = httpx.Client(transport=transport)
    client = DexCrmClient(
        config=DexCrmClientConfig(rate_limit_sleep_s=0.0),
        http_client=inner_client,
        auth=_ScriptedAuth(),
        sleep=lambda _s: None,
    )
    return DexCrmConnector(client=client)


def test_dex_crm_envelope_metadata_lands_on_chunk(tmp_path: Path) -> None:
    """DexCrmConnector.metadata_for surfaces modified_by + updated_at + tags onto the chunk."""
    connector = _build_connector()
    db_path = tmp_path / "dex_metadata.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    chunk_writer = FakeChunkWriter()
    pipeline = factory.build_connector_pipeline(
        db=db,
        collection="dex-metadata-propagation",
        chunk_writer=chunk_writer,
        entity_graph_sink=FakeEntityGraphSink(),
    )

    pipeline.run_batch(connector, FakeExtractor())

    chunks = [chunk for batch in chunk_writer.writes for chunk in batch]
    assert chunks, "DexCrmConnector did not surface any chunks"
    authors = [chunk.author for chunk in chunks]
    assert "agent-alpha" in authors, (
        f"expected envelope modified_by='agent-alpha' on at least one chunk; got {authors!r}"
    )
    chunk_dates = [chunk.source_modified_at for chunk in chunks]
    assert "2026-05-28T09:00:00Z" in chunk_dates, (
        f"expected envelope updated_at='2026-05-28T09:00:00Z' on chunk_date; got {chunk_dates!r}"
    )
    all_tags: set[str] = set()
    for chunk in chunks:
        all_tags.update(chunk.tags)
    assert "design" in all_tags, f"expected envelope tag 'design' on chunk; got {sorted(all_tags)!r}"
