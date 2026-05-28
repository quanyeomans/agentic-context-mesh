"""Step impls for documents_media_writer.feature (GH #336 / ADR-024 Bundle B).

The scenarios drive the real :class:`kairix.core.connectors.pipeline.ConnectorPipeline`
through :func:`kairix.core.factory.build_connector_pipeline` (F46 / F47
compliant) and assert directly against the ``documents_media`` table
after one batch. Three branches mirror the integration test:

* happy_path  — extractor returns + quality_ok True  -> status='ok'
* failure     — extractor raises                     -> status='failed' AND dead-letter
* unsupported — extractor returns + quality_ok False -> status='unsupported'

F1-clean: stores constructed via public constructors + injected via
the factory's documented kwargs. F2-clean: no ``KAIRIX_*`` env-var
manipulation.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.core import factory
from kairix.core.db.schema import create_schema
from kairix.core.protocols import ChangeEvent, DocMetadata, ExtractedDocument
from tests.fakes import FakeChunkWriter, FakeEntityGraphSink, FakeExtractor, FakeSourceConnector


class _BddRaisingExtractor:
    name: str = "raising-extractor"
    version: str = "v0.bdd"

    def can_extract(self, _mime: str, _magic: bytes) -> bool:
        return True

    def extract(self, _raw: bytes, _mime: str) -> Any:
        raise RuntimeError("scripted BDD failure: corrupt PDF")

    def quality_ok(self, _doc: Any) -> bool:  # pragma: no cover — extract raises first
        return False

    def metadata_for(self, _raw: bytes, _mime: str) -> Any:
        from kairix.core.protocols import SourceMetadata

        return SourceMetadata()


class _BddUnsupportedExtractor:
    name: str = "unsupported-extractor"
    version: str = "v0.bdd"

    def can_extract(self, _mime: str, _magic: bytes) -> bool:
        return True

    def extract(self, raw: bytes, _mime: str) -> ExtractedDocument:
        text = raw.decode("utf-8", errors="replace") or "unsupported-bdd"
        return ExtractedDocument(
            markdown=text,
            pages=(),
            images=(),
            metadata=DocMetadata(title=None, author=None, created_date=None, language=None, page_count=None),
            confidence=0.1,
        )

    def quality_ok(self, _doc: ExtractedDocument) -> bool:
        return False

    def metadata_for(self, _raw: bytes, _mime: str) -> Any:
        from kairix.core.protocols import SourceMetadata

        return SourceMetadata()


@dataclass
class _ScenarioState:
    db: sqlite3.Connection
    pipeline: Any
    source_name: str = ""
    extractor: Any = None
    events: list[ChangeEvent] = field(default_factory=list)
    content: dict[str, bytes] = field(default_factory=dict)


@pytest.fixture
def media_state(tmp_path: Path) -> _ScenarioState:
    db_path = tmp_path / "documents_media_bdd.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    pipeline = factory.build_connector_pipeline(
        db=db,
        collection="documents-media-bdd",
        chunk_writer=FakeChunkWriter(),
        entity_graph_sink=FakeEntityGraphSink(),
    )
    return _ScenarioState(db=db, pipeline=pipeline)


@given(parsers.parse('a connector "{name}" with one markdown change event'))
def given_happy_connector(media_state: _ScenarioState, name: str) -> None:
    media_state.source_name = name
    media_state.events = [
        ChangeEvent(op="modified", item_id="doc-1.md", modified_at="2026-05-28T10:00:00Z"),
    ]
    media_state.content = {"doc-1.md": ("Body text. " * 30).encode("utf-8")}


@given(parsers.parse('a connector "{name}" with one corrupt-PDF change event'))
def given_failed_connector(media_state: _ScenarioState, name: str) -> None:
    media_state.source_name = name
    media_state.events = [
        ChangeEvent(op="modified", item_id="corrupt.pdf", modified_at="2026-05-28T10:00:00Z"),
    ]
    media_state.content = {"corrupt.pdf": b"%PDF-1.4 corrupt..."}


@given(parsers.parse('a connector "{name}" with one video-like change event'))
def given_unsupported_connector(media_state: _ScenarioState, name: str) -> None:
    media_state.source_name = name
    media_state.events = [
        ChangeEvent(op="modified", item_id="video.mp4", modified_at="2026-05-28T10:00:00Z"),
    ]
    media_state.content = {"video.mp4": b"low-quality body text"}


@given("the configured extractor is the canonical FakeExtractor")
def given_fake_extractor(media_state: _ScenarioState) -> None:
    media_state.extractor = FakeExtractor()


@given("the configured extractor raises on extract")
def given_raising_extractor(media_state: _ScenarioState) -> None:
    media_state.extractor = _BddRaisingExtractor()


@given("the configured extractor reports quality_ok=False")
def given_unsupported_extractor(media_state: _ScenarioState) -> None:
    media_state.extractor = _BddUnsupportedExtractor()


@when(parsers.parse('the operator runs one pipeline batch for "{name}"'))
def when_run_batch(media_state: _ScenarioState, name: str) -> None:
    assert media_state.extractor is not None, "extractor not configured in Given step"
    assert media_state.source_name == name, (
        f"source mismatch: scenario uses {media_state.source_name!r}, asked for {name!r}"
    )
    connector = FakeSourceConnector(
        name=media_state.source_name,
        events=media_state.events,
        content=media_state.content,
        cursor_token=f"{media_state.source_name}-cursor-1",
    )
    media_state.pipeline.run_batch(connector, media_state.extractor)


def _fetch_media_status(state: _ScenarioState) -> str | None:
    row = state.db.execute("SELECT extraction_status FROM documents_media LIMIT 1").fetchone()
    return None if row is None else str(row[0])


@then(parsers.parse('a documents_media row exists with extraction_status "{status}"'))
def then_status(media_state: _ScenarioState, status: str) -> None:
    actual = _fetch_media_status(media_state)
    assert actual is not None, "no documents_media row found"
    assert actual == status, f"expected extraction_status={status!r}; got {actual!r}"


@then(parsers.parse('that row carries the extractor name "{name}"'))
def then_extractor_name(media_state: _ScenarioState, name: str) -> None:
    row = media_state.db.execute("SELECT extractor_name FROM documents_media LIMIT 1").fetchone()
    assert row is not None and row[0] == name, f"expected extractor_name={name!r}; got {row!r}"


@then(parsers.parse('that row carries the extractor version "{version}"'))
def then_extractor_version(media_state: _ScenarioState, version: str) -> None:
    row = media_state.db.execute("SELECT extractor_version FROM documents_media LIMIT 1").fetchone()
    assert row is not None and row[0] == version, f"expected extractor_version={version!r}; got {row!r}"


@then("that row carries the failing extractor identity")
def then_failing_extractor_identity(media_state: _ScenarioState) -> None:
    row = media_state.db.execute("SELECT extractor_name, extractor_version FROM documents_media LIMIT 1").fetchone()
    assert row is not None, "no documents_media row"
    assert row[0] == "raising-extractor", f"expected extractor_name=raising-extractor; got {row[0]!r}"
    assert row[1] == "v0.bdd", f"expected extractor_version=v0.bdd; got {row[1]!r}"


@then("the item appears in the connector_deadletter table")
def then_in_deadletter(media_state: _ScenarioState) -> None:
    row = media_state.db.execute(
        "SELECT COUNT(*) FROM connector_deadletter WHERE source_name = ?",
        (media_state.source_name,),
    ).fetchone()
    assert row is not None and row[0] >= 1, (
        f"expected at least one connector_deadletter row for {media_state.source_name!r}; got {row!r}"
    )


@then("the item does NOT appear in the connector_deadletter table")
def then_not_in_deadletter(media_state: _ScenarioState) -> None:
    row = media_state.db.execute(
        "SELECT COUNT(*) FROM connector_deadletter WHERE source_name = ?",
        (media_state.source_name,),
    ).fetchone()
    assert row is not None and row[0] == 0, (
        f"expected zero connector_deadletter rows for {media_state.source_name!r}; got {row!r}"
    )
