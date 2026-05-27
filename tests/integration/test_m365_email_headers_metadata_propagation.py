"""M365 email-headers envelope metadata propagation — ADR-021 / F65.

:class:`kairix.connectors.m365_email_headers.M365EmailHeadersConnector`
exposes ``sender`` + ``received_at`` + ``to_recipients`` per Graph
envelope; ``metadata_for`` lifts those onto the
:class:`SourceMetadata` payload and silver threads it through to the
indexed :class:`~kairix.core.protocols.Chunk`.

Sabotage proof: clear ``sender`` on the scripted GraphMessage; assert
``chunk.author`` becomes None; restore.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from kairix.connectors.m365_email_headers import M365EmailHeadersConnector
from kairix.connectors.m365_email_headers.connector import M365Credentials
from kairix.connectors.m365_email_headers.graph_client import GraphMessage, M365GraphClient
from kairix.core import factory
from kairix.core.db.schema import create_schema
from kairix.transport.auth.oauth2_client_creds import OAuth2ClientCredsAuth
from tests.fakes import FakeChunkWriter, FakeEntityGraphSink, FakeExtractor

pytestmark = pytest.mark.integration


class _ScriptedGraphClient(M365GraphClient):
    def __init__(self, mailbox: str) -> None:
        self._mailbox = mailbox
        self._delta: str | None = None

    def iter_messages(self, start_url: str | None = None) -> Iterator[GraphMessage]:
        del start_url
        self._delta = f"https://graph.microsoft.com/v1.0/users/{self._mailbox}/messages/delta?$deltatoken=metadata-tok"
        yield GraphMessage(
            message_id="msg-metadata-1",
            sender="agent-alpha@example.com",
            to_recipients=("recipient@example.com",),
            cc_recipients=(),
            subject="Envelope-bearing email",
            sent_at="2026-05-28T07:00:00Z",
            received_at="2026-05-28T07:00:01Z",
        )

    def last_delta_link(self) -> str | None:
        return self._delta


def _stub_client_builder(_auth: OAuth2ClientCredsAuth, upn: str) -> M365GraphClient:
    return _ScriptedGraphClient(mailbox=upn)


def test_m365_email_headers_envelope_metadata_lands_on_chunk(tmp_path: Path) -> None:
    """M365EmailHeadersConnector.metadata_for surfaces sender + received_at + recipients."""
    connector = M365EmailHeadersConnector(
        user_principal_name="agent-alpha@example.com",
        credentials=M365Credentials(
            tenant_id="fake-tenant",
            client_id="fake-client",
            client_secret="fake-secret-value",  # pragma: allowlist secret — test fixture
        ),
        client_builder=_stub_client_builder,
    )
    db_path = tmp_path / "m365_email_metadata.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    chunk_writer = FakeChunkWriter()
    pipeline = factory.build_connector_pipeline(
        db=db,
        collection="m365-email-metadata-propagation",
        chunk_writer=chunk_writer,
        entity_graph_sink=FakeEntityGraphSink(),
    )

    pipeline.run_batch(connector, FakeExtractor())

    chunks = [chunk for batch in chunk_writer.writes for chunk in batch]
    assert chunks, "M365EmailHeadersConnector did not surface any chunks"
    authors = [chunk.author for chunk in chunks]
    assert "agent-alpha@example.com" in authors, (
        f"expected envelope sender 'agent-alpha@example.com' on chunk.author; got {authors!r}"
    )
    chunk_dates = [chunk.source_modified_at for chunk in chunks]
    assert "2026-05-28T07:00:01Z" in chunk_dates, f"expected envelope received_at on chunk_date; got {chunk_dates!r}"
    all_tags: set[str] = set()
    for chunk in chunks:
        all_tags.update(chunk.tags)
    assert "recipient@example.com" in all_tags, f"expected to_recipients in chunk.tags; got {sorted(all_tags)!r}"
