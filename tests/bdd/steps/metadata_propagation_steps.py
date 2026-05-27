"""Step implementations for connector_metadata_propagation.feature (ADR-021 / F65).

The scenarios drive the production
:class:`kairix.core.connectors.pipeline.ConnectorPipeline` through
:func:`kairix.core.factory.build_connector_pipeline` against scripted
:class:`tests.fakes.FakeSourceConnector` instances that surface
:class:`kairix.core.protocols.SourceMetadata` envelope payloads. The
:class:`tests.fakes.FakeExtractor` carries an optional body-derived
metadata override so the silver-merge precedence is observable end-to-
end.

Per F46 the steps reach the pipeline through its documented factory
seam — no direct ``*Pipeline(...)`` construction, no monkey-patching,
no env-var manipulation.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from pytest_bdd import given, scenarios, then, when

from kairix.core import factory
from kairix.core.connectors.pipeline import ConnectorPipeline
from kairix.core.db.schema import create_schema
from kairix.core.protocols import ChangeEvent, SourceMetadata
from tests.fakes import FakeChunkWriter, FakeEntityGraphSink, FakeExtractor, FakeSourceConnector

pytestmark = pytest.mark.bdd

scenarios("../features/connector_metadata_propagation.feature")


_ENVELOPE_MODIFIED_AT = "2026-05-28T09:15:00Z"


@dataclass
class _ScenarioState:
    db: sqlite3.Connection
    pipeline: ConnectorPipeline
    chunk_writer: FakeChunkWriter
    connector: FakeSourceConnector | None = None
    extractor: FakeExtractor | None = None
    events: list[ChangeEvent] = field(default_factory=list)


@pytest.fixture
def metadata_state(tmp_path: Path) -> _ScenarioState:
    db_path = tmp_path / "metadata_propagation.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    chunk_writer = FakeChunkWriter()
    pipeline = factory.build_connector_pipeline(
        db=db,
        collection="metadata-propagation",
        chunk_writer=chunk_writer,
        entity_graph_sink=FakeEntityGraphSink(),
    )
    return _ScenarioState(db=db, pipeline=pipeline, chunk_writer=chunk_writer)


@given('a configured connector emitting one item with envelope author "agent-alpha"')
def given_envelope_author_connector(metadata_state: _ScenarioState) -> None:
    item_id = "envelope-doc.md"
    body = b"# Envelope-author doc\n\nbody paragraph carrying searchable text."
    event = ChangeEvent(op="created", item_id=item_id, modified_at="2026-05-28T08:00:00Z")
    metadata_state.events = [event]
    metadata_state.connector = FakeSourceConnector(
        name="envelope-author-source",
        events=metadata_state.events,
        content={item_id: body},
        cursor_token="envelope-cursor-1",
        metadata={item_id: SourceMetadata(author="agent-alpha", modified_at=_ENVELOPE_MODIFIED_AT)},
    )


@given('a configured connector emitting one item with envelope author "envelope-author"')
def given_envelope_only_author(metadata_state: _ScenarioState) -> None:
    item_id = "collision-doc.md"
    body = b"# Collision doc\n\nbody paragraph carrying searchable text."
    event = ChangeEvent(op="created", item_id=item_id, modified_at="2026-05-28T08:00:00Z")
    metadata_state.events = [event]
    metadata_state.connector = FakeSourceConnector(
        name="collision-source",
        events=metadata_state.events,
        content={item_id: body},
        cursor_token="collision-cursor-1",
        metadata={item_id: SourceMetadata(author="envelope-author", modified_at=_ENVELOPE_MODIFIED_AT)},
    )


@given('the configured extractor surfaces document-body author "body-author"')
def given_body_author_extractor(metadata_state: _ScenarioState) -> None:
    metadata_state.extractor = FakeExtractor(metadata=SourceMetadata(author="body-author"))


@when("the operator runs one pipeline batch through the factory")
def when_pipeline_runs(metadata_state: _ScenarioState) -> None:
    extractor = metadata_state.extractor or FakeExtractor()
    assert metadata_state.connector is not None
    metadata_state.pipeline.run_batch(metadata_state.connector, extractor)


@then('the indexed chunk carries the author "agent-alpha"')
def then_chunk_author_alpha(metadata_state: _ScenarioState) -> None:
    chunks = [chunk for batch in metadata_state.chunk_writer.writes for chunk in batch]
    assert chunks, "no chunks were upserted — pipeline did not surface the envelope metadata"
    authors = [chunk.author for chunk in chunks]
    assert "agent-alpha" in authors, f"expected agent-alpha author on at least one chunk; got {authors!r}"


@then("the indexed chunk carries the envelope's modified-at as the chunk date")
def then_chunk_modified_at(metadata_state: _ScenarioState) -> None:
    chunks = [chunk for batch in metadata_state.chunk_writer.writes for chunk in batch]
    assert chunks, "no chunks were upserted — pipeline did not surface the envelope metadata"
    modified_at_values = [chunk.source_modified_at for chunk in chunks]
    assert _ENVELOPE_MODIFIED_AT in modified_at_values, (
        f"expected envelope modified_at={_ENVELOPE_MODIFIED_AT!r} on at least one chunk; got {modified_at_values!r}"
    )


@then('the indexed chunk carries the author "envelope-author"')
def then_chunk_author_envelope(metadata_state: _ScenarioState) -> None:
    chunks = [chunk for batch in metadata_state.chunk_writer.writes for chunk in batch]
    assert chunks, "no chunks were upserted — pipeline did not surface the envelope metadata"
    authors = [chunk.author for chunk in chunks]
    assert "envelope-author" in authors, f"expected envelope-author to beat body-author on collision; got {authors!r}"
    assert "body-author" not in authors, f"body-author should not appear when envelope wins; got {authors!r}"
