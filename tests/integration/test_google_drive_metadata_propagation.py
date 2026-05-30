"""Google Drive envelope metadata propagation — ADR-021 / F65.

:class:`kairix.connectors.google_drive.GoogleDriveConnector` lifts the
Drive file envelope (``lastModifyingUser.emailAddress`` /
``modifiedTime`` / owner emails) onto the
:class:`~kairix.core.protocols.SourceMetadata` payload; silver threads
it through to the indexed :class:`~kairix.core.protocols.Chunk`.

Sabotage proof: drop ``lastModifyingUser`` from the scripted envelope;
assert ``chunk.author`` / ``chunk.author_email`` become None; restore.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
import pytest

from kairix.connectors.google_drive import (
    GoogleDriveClient,
    GoogleDriveConnector,
    GoogleDriveCorpusSpec,
    GoogleDriveCredentials,
)
from kairix.core import factory
from kairix.core.db.schema import create_schema
from tests.fakes import FakeChunkWriter, FakeEntityGraphSink, FakeExtractor

pytestmark = pytest.mark.integration

_CORPUS_ID = "workspace-metadata"


def _build_envelope() -> dict[str, object]:
    return {
        "id": "file-metadata-1",
        "name": "envelope-doc.md",
        "size": "256",
        "modifiedTime": "2026-05-28T09:30:00Z",
        "createdTime": "2026-05-20T10:00:00Z",
        "webViewLink": "https://drive.google.com/file/d/file-metadata-1/view",
        "mimeType": "text/markdown",
        "lastModifyingUser": {
            "emailAddress": "agent-alpha@example.com",
            "displayName": "agent-alpha",
        },
        "owners": [{"emailAddress": "agent-beta@example.com"}],
    }


def _build_connector() -> GoogleDriveConnector:
    body = {
        "newStartPageToken": "metadata-tok",
        "changes": [
            {
                "fileId": "file-metadata-1",
                "file": _build_envelope(),
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/changes/startPageToken" in url:
            return httpx.Response(200, json={"startPageToken": "seed-metadata"})
        if "alt=media" in url:
            return httpx.Response(
                200,
                content=b"# Envelope-bearing doc\n\nbody paragraph.",
                headers={"Content-Type": "text/markdown"},
            )
        return httpx.Response(200, json=body)

    shared = httpx.Client(transport=httpx.MockTransport(handler))
    return GoogleDriveConnector(
        corpora=[GoogleDriveCorpusSpec(corpus_id=_CORPUS_ID)],
        credentials=GoogleDriveCredentials(
            access_token="fake-token-value",  # pragma: allowlist secret — integration test fixture
        ),
        client_builder=lambda creds: GoogleDriveClient(access_token=creds.access_token, http_client=shared),
    )


def test_google_drive_envelope_metadata_lands_on_chunk(tmp_path: Path) -> None:
    """GoogleDriveConnector.metadata_for surfaces lastModifyingUser + modifiedTime."""
    connector = _build_connector()
    db_path = tmp_path / "google_drive_metadata.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    chunk_writer = FakeChunkWriter()
    pipeline = factory.build_connector_pipeline(
        db=db,
        collection="google-drive-metadata-propagation",
        chunk_writer=chunk_writer,
        entity_graph_sink=FakeEntityGraphSink(),
    )

    pipeline.run_batch(connector, FakeExtractor())

    chunks = [chunk for batch in chunk_writer.writes for chunk in batch]
    assert chunks, "GoogleDriveConnector did not surface any chunks"
    authors = [chunk.author for chunk in chunks]
    assert "agent-alpha" in authors, (
        f"expected envelope lastModifyingUser.displayName 'agent-alpha' on chunk.author; got {authors!r}"
    )
    author_emails = [chunk.author_email for chunk in chunks]
    assert "agent-alpha@example.com" in author_emails, (
        f"expected envelope lastModifyingUser.emailAddress on chunk.author_email; got {author_emails!r}"
    )
    chunk_dates = [chunk.source_modified_at for chunk in chunks]
    assert "2026-05-28T09:30:00Z" in chunk_dates, f"expected envelope modifiedTime on chunk_date; got {chunk_dates!r}"
