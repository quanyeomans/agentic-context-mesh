"""SharePoint envelope metadata propagation — ADR-021 / F65.

:class:`kairix.connectors.sharepoint.SharePointConnector` lifts the
Graph drive-item envelope (``createdBy.user.displayName`` /
``lastModifiedDateTime`` / parent path segments) onto the
:class:`SourceMetadata` payload; silver threads it through to the
indexed :class:`~kairix.core.protocols.Chunk`.

Sabotage proof: drop ``createdBy`` from the scripted envelope; assert
``chunk.author`` becomes None; restore.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
import pytest

from kairix.connectors.sharepoint import (
    SharePointConnector,
    SharePointCredentials,
    SharePointDriveSpec,
    SharePointGraphClient,
)
from kairix.core import factory
from kairix.core.db.schema import create_schema
from kairix.transport.auth.oauth2_client_creds import OAuth2ClientCredsAuth
from tests.fakes import FakeChunkWriter, FakeEntityGraphSink, FakeExtractor

pytestmark = pytest.mark.integration

_DRIVE_ID = "b!metadata-drive"


def _build_envelope() -> dict[str, object]:
    return {
        "id": "item-metadata-1",
        "name": "envelope-doc.md",
        "size": 256,
        "lastModifiedDateTime": "2026-05-28T09:30:00Z",
        "createdDateTime": "2026-05-20T10:00:00Z",
        "webUrl": "https://example.sharepoint.com/sites/team/Documents/Curated-Content/envelope-doc.md",
        "file": {"mimeType": "text/markdown"},
        "parentReference": {"driveId": _DRIVE_ID, "path": f"/drives/{_DRIVE_ID}/root:/Curated-Content"},
        "createdBy": {"user": {"displayName": "agent-alpha", "email": "agent-alpha@example.com"}},
        "lastModifiedBy": {"user": {"displayName": "agent-beta", "email": "agent-beta@example.com"}},
    }


def _build_connector() -> SharePointConnector:
    body = {
        "@odata.context": f"https://graph.microsoft.com/v1.0/$metadata#drives/{_DRIVE_ID}/root/delta",
        "value": [_build_envelope()],
        "@odata.deltaLink": f"https://graph.microsoft.com/v1.0/drives/{_DRIVE_ID}/root/delta?$deltatoken=metadata-tok",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/oauth2/v2.0/token" in url:
            return httpx.Response(
                200,
                json={"access_token": "tok", "expires_in": 3600, "token_type": "Bearer"},
            )
        if "/items/" in url and "/content" in url:
            return httpx.Response(200, content=b"# Envelope-bearing doc\n\nbody paragraph.")
        return httpx.Response(200, json=body)

    shared = httpx.Client(transport=httpx.MockTransport(handler))
    auth = OAuth2ClientCredsAuth(
        tenant_id="t",
        client_id="c",
        client_secret="s-value",  # pragma: allowlist secret — integration test fixture
        scope="https://graph.microsoft.com/.default",
        http_client=shared,
    )
    return SharePointConnector(
        drives=[SharePointDriveSpec(drive_id=_DRIVE_ID)],
        credentials=SharePointCredentials(
            tenant_id="t",
            client_id="c",
            client_secret="s-value",  # pragma: allowlist secret — integration test fixture
        ),
        auth=auth,
        client_builder=lambda a: SharePointGraphClient(auth=a, http_client=shared),
    )


def test_sharepoint_envelope_metadata_lands_on_chunk(tmp_path: Path) -> None:
    """SharePointConnector.metadata_for surfaces createdBy + lastModifiedDateTime + path tags."""
    connector = _build_connector()
    db_path = tmp_path / "sharepoint_metadata.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    chunk_writer = FakeChunkWriter()
    pipeline = factory.build_connector_pipeline(
        db=db,
        collection="sharepoint-metadata-propagation",
        chunk_writer=chunk_writer,
        entity_graph_sink=FakeEntityGraphSink(),
    )

    pipeline.run_batch(connector, FakeExtractor())

    chunks = [chunk for batch in chunk_writer.writes for chunk in batch]
    assert chunks, "SharePointConnector did not surface any chunks"
    authors = [chunk.author for chunk in chunks]
    assert "agent-alpha" in authors, (
        f"expected envelope createdBy.displayName 'agent-alpha' on chunk.author; got {authors!r}"
    )
    chunk_dates = [chunk.source_modified_at for chunk in chunks]
    assert "2026-05-28T09:30:00Z" in chunk_dates, (
        f"expected envelope lastModifiedDateTime on chunk_date; got {chunk_dates!r}"
    )
    all_tags: set[str] = set()
    for chunk in chunks:
        all_tags.update(chunk.tags)
    assert "Curated-Content" in all_tags, (
        f"expected parent-path segment 'Curated-Content' in chunk.tags; got {sorted(all_tags)!r}"
    )
