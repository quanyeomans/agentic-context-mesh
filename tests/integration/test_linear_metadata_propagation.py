"""Linear envelope metadata propagation — ADR-021 / F65.

:class:`kairix.connectors.linear.LinearConnector` lifts the Linear node
envelope (creator → author, createdAt/updatedAt → dates, labels → tags,
state/team/project → properties) onto the :class:`SourceMetadata`
payload; silver threads it through to the indexed
:class:`~kairix.core.protocols.Chunk`.

Sabotage proof (executed by the agent, restored on completion): mutate
``LinearConnector.metadata_for`` to return ``SourceMetadata()`` (drop the
author lift); assert ``chunk.author`` becomes ``None``; the test fails;
restore.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from kairix.connectors.linear import LinearConnector, LinearCredentials
from kairix.core import factory
from kairix.core.db.schema import create_schema
from tests.fakes import FakeChunkWriter, FakeEntityGraphSink, FakeExtractor, FakeLinearApiClient

pytestmark = pytest.mark.integration

_UPDATED_AT = "2026-05-28T08:30:00.000Z"


def _issue_node() -> dict[str, Any]:
    return {
        "id": "uuid-issue-metadata-1",
        "identifier": "ENG-501",
        "title": "Envelope-bearing issue",
        "description": "The issue body that becomes chunk content.",
        "url": "https://linear.app/your-team/issue/ENG-501",
        "createdAt": "2026-05-26T09:00:00.000Z",
        "updatedAt": _UPDATED_AT,
        "state": {"name": "In Progress"},
        "creator": {"displayName": "agent-alpha", "email": "agent-alpha@example.com"},
        "team": {"name": "Engineering"},
        "project": {"id": "uuid-proj-1", "name": "Roadmap recall"},
        "labels": {"nodes": [{"name": "roadmap"}, {"name": "p1"}]},
    }


def test_linear_envelope_metadata_lands_on_chunk(tmp_path: Path) -> None:
    """LinearConnector.metadata_for surfaces author + chunk_date onto the chunk."""
    api = FakeLinearApiClient(pages={"issues": [[_issue_node()]]})
    connector = LinearConnector(
        credentials=LinearCredentials(api_key="lin_metadata_fixture"),  # pragma: allowlist secret — test fixture
        client_builder=lambda _c: api,
    )
    db_path = tmp_path / "linear_metadata.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    chunk_writer = FakeChunkWriter()
    pipeline = factory.build_connector_pipeline(
        db=db,
        collection="linear-metadata-propagation",
        chunk_writer=chunk_writer,
        entity_graph_sink=FakeEntityGraphSink(),
    )

    pipeline.run_batch(connector, FakeExtractor())

    chunks = [chunk for batch in chunk_writer.writes for chunk in batch]
    assert chunks, "LinearConnector did not surface any chunks"

    authors = [chunk.author for chunk in chunks]
    assert "agent-alpha" in authors, f"expected envelope author 'agent-alpha' on chunk.author; got {authors!r}"

    # chunk_date rides source_modified_at (the issue's updatedAt envelope).
    chunk_dates = [chunk.source_modified_at for chunk in chunks]
    assert _UPDATED_AT in chunk_dates, (
        f"expected envelope updatedAt on chunk.source_modified_at (chunk_date); got {chunk_dates!r}"
    )

    all_tags: set[str] = set()
    for chunk in chunks:
        all_tags.update(chunk.tags)
    assert "roadmap" in all_tags, f"expected label 'roadmap' in chunk.tags; got {sorted(all_tags)!r}"
