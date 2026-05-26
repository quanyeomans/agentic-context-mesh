"""Step definitions for connector_pipeline_failure_modes.feature.

Drives the real ConnectorPipeline via the F47-clean
``build_connector_pipeline`` factory. Scripted-failure connectors,
extractors, and writers are F1-clean Protocol impls (no monkeypatching
of kairix internals).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.core.db.schema import create_schema
from kairix.core.factory import build_connector_pipeline
from kairix.core.protocols import ChangeEvent, DocMetadata, ExtractedDocument, MimeType
from tests.fakes import FakeChunkWriter, FakeEntityGraphSink, FakeExtractor, FakeSourceConnector

pytestmark = pytest.mark.bdd


@dataclass
class _PipelineCtx:
    db: Any = None
    bronze_root: Path | None = None
    source: Any = None
    extractor: Any = None
    writer: Any = None
    result: Any = None
    exception: BaseException | None = None
    events: list[ChangeEvent] = field(default_factory=list)
    contents: dict[str, bytes] = field(default_factory=dict)


@pytest.fixture
def pipe_ctx(tmp_path: Path) -> _PipelineCtx:
    ctx = _PipelineCtx()
    ctx.db = sqlite3.connect(":memory:")
    create_schema(ctx.db)
    ctx.bronze_root = tmp_path / "bronze"
    return ctx


# ---------------------------------------------------------------------------
# Scripted-failure components
# ---------------------------------------------------------------------------


@dataclass
class _ExtractorRaisingOnNthCall:
    name: str = "raising-extractor"
    version: str = "1.0.0"
    fail_on_call_n: int = 5
    calls: int = 0

    def can_extract(self, mime: MimeType, magic_bytes: bytes) -> bool:
        return True

    def extract(self, raw: bytes, mime: MimeType) -> ExtractedDocument:
        self.calls += 1
        if self.calls == self.fail_on_call_n:
            raise RuntimeError(f"extract-raise-on-call-{self.calls}")
        return ExtractedDocument(
            markdown=f"# Body call {self.calls}\n\nbody content",
            pages=(),
            images=(),
            metadata=DocMetadata(title=None, author=None, created_date=None, language=None, page_count=None),
            confidence=0.5,
        )

    def quality_ok(self, doc: ExtractedDocument) -> bool:
        return True


@dataclass
class _WriterRaisingOnNthCall:
    fail_on_call_n: int = 51
    calls: int = 0

    def upsert(self, chunks: Any) -> int:
        self.calls += 1
        if self.calls == self.fail_on_call_n:
            raise RuntimeError(f"writer-raise-on-call-{self.calls}")
        return 0


class _StopAfterNListChangesConnector(FakeSourceConnector):
    def __init__(self, *, events: list[ChangeEvent], content: dict[str, bytes], raise_after_n: int) -> None:
        super().__init__(name="raising-list-changes-source", events=events, content=content)
        self._raise_after_n = raise_after_n

    def list_changes(self, cursor: Any = None) -> Any:
        emitted = 0
        for event in self._events:  # type: ignore[attr-defined] — FakeSourceConnector stores events as _events; we subclass to override list_changes
            if emitted >= self._raise_after_n:
                raise RuntimeError(f"list-changes-raise-after-{self._raise_after_n}")
            yield event
            emitted += 1


# ---------------------------------------------------------------------------
# Given
# ---------------------------------------------------------------------------


@given(parsers.parse("a connector that yields {n:d} change events"))
def given_connector_yields_n(pipe_ctx: _PipelineCtx, n: int) -> None:
    pipe_ctx.events = [
        ChangeEvent(op="created", item_id=f"item-{i:03d}", modified_at=f"2026-01-01T00:00:{i:02d}Z") for i in range(n)
    ]
    pipe_ctx.contents = {f"item-{i:03d}": f"payload-{i}".encode() for i in range(n)}
    pipe_ctx.source = FakeSourceConnector(
        name="failure-modes-source", events=pipe_ctx.events, content=pipe_ctx.contents
    )


@given(parsers.parse('the connector is scripted to fail fetch on "{item_id}"'))
def given_fetch_fails_on_item(pipe_ctx: _PipelineCtx, item_id: str) -> None:
    pipe_ctx.source = FakeSourceConnector(
        name="failure-modes-source",
        events=pipe_ctx.events,
        content=pipe_ctx.contents,
        fail_on_fetch={item_id},
    )


@given(parsers.parse("an extractor that raises on the {n:d}{ordinal} call"))
def given_extractor_raises_on_nth(pipe_ctx: _PipelineCtx, n: int, ordinal: str) -> None:
    pipe_ctx.extractor = _ExtractorRaisingOnNthCall(fail_on_call_n=n)


@given(parsers.parse("a chunk writer that raises on its {n:d}{ordinal} call"))
def given_writer_raises_on_nth(pipe_ctx: _PipelineCtx, n: int, ordinal: str) -> None:
    pipe_ctx.writer = _WriterRaisingOnNthCall(fail_on_call_n=n)


@given(parsers.parse("a connector whose list_changes raises after yielding {n:d} events"))
def given_list_changes_raises_after_n(pipe_ctx: _PipelineCtx, n: int) -> None:
    # Build with default 10 events; raise_after_n controls when it explodes
    events = [
        ChangeEvent(op="created", item_id=f"item-{i:03d}", modified_at=f"2026-01-01T00:00:{i:02d}Z") for i in range(10)
    ]
    contents = {f"item-{i:03d}": f"payload-{i}".encode() for i in range(10)}
    pipe_ctx.source = _StopAfterNListChangesConnector(events=events, content=contents, raise_after_n=n)


# ---------------------------------------------------------------------------
# When
# ---------------------------------------------------------------------------


@when(parsers.parse("the connector pipeline runs the batch"))
def when_run_batch(pipe_ctx: _PipelineCtx) -> None:
    extractor = pipe_ctx.extractor if pipe_ctx.extractor is not None else FakeExtractor()
    writer = pipe_ctx.writer if pipe_ctx.writer is not None else FakeChunkWriter()
    pipeline = build_connector_pipeline(
        db=pipe_ctx.db,
        bronze_root=pipe_ctx.bronze_root,
        collection="default",
        chunk_writer=writer,
        entity_graph_sink=FakeEntityGraphSink(),
    )
    try:
        pipe_ctx.result = pipeline.run_batch(pipe_ctx.source, extractor)
    except Exception as exc:
        pipe_ctx.exception = exc


# ---------------------------------------------------------------------------
# Then
# ---------------------------------------------------------------------------


@then(parsers.parse("{n:d} items are processed successfully"))
def then_n_processed(pipe_ctx: _PipelineCtx, n: int) -> None:
    assert pipe_ctx.result is not None, f"expected a result; got exception={pipe_ctx.exception}"
    assert pipe_ctx.result.processed == n, f"expected {n} processed, got {pipe_ctx.result.processed}"


@then(parsers.parse("{n:d} item is recorded in the dead-letter store"))
def then_n_dead_lettered(pipe_ctx: _PipelineCtx, n: int) -> None:
    assert pipe_ctx.result is not None
    assert pipe_ctx.result.dead_lettered == n, f"expected {n} dead-lettered, got {pipe_ctx.result.dead_lettered}"


@then(parsers.parse("{n:d} items are recorded in the dead-letter store"))
def then_n_dead_lettered_plural(pipe_ctx: _PipelineCtx, n: int) -> None:
    assert pipe_ctx.result is not None
    assert pipe_ctx.result.dead_lettered == n, f"expected {n} dead-lettered, got {pipe_ctx.result.dead_lettered}"


@then(parsers.parse('the dead-letter row for "{item_id}" carries a "{prefix}" error prefix'))
def then_dead_letter_prefix(pipe_ctx: _PipelineCtx, item_id: str, prefix: str) -> None:
    row = pipe_ctx.db.execute(
        "SELECT item_id, last_error FROM connector_deadletter "
        "WHERE source_name = 'failure-modes-source' AND item_id = ?",
        (item_id,),
    ).fetchone()
    assert row is not None, f"no dead_letter row for {item_id}"
    assert prefix in row[1].lower(), f"expected '{prefix}' in error, got: {row[1]}"


@then(parsers.parse('the dead-letter row carries an "{prefix}" error prefix'))
def then_dead_letter_prefix_any(pipe_ctx: _PipelineCtx, prefix: str) -> None:
    row = pipe_ctx.db.execute(
        "SELECT last_error FROM connector_deadletter WHERE source_name = 'failure-modes-source' LIMIT 1"
    ).fetchone()
    assert row is not None
    assert prefix in row[0].lower(), f"expected '{prefix}' in error, got: {row[0]}"


@then(parsers.parse("a RuntimeError propagates to the caller"))
def then_runtime_error_propagates(pipe_ctx: _PipelineCtx) -> None:
    assert isinstance(pipe_ctx.exception, RuntimeError), (
        f"expected RuntimeError; got exception={pipe_ctx.exception!r}, result={pipe_ctx.result}"
    )


@then(parsers.parse("the bronze_records table contains exactly {n:d} rows for that source"))
def then_bronze_count(pipe_ctx: _PipelineCtx, n: int) -> None:
    count = pipe_ctx.db.execute(
        "SELECT COUNT(*) FROM bronze_records WHERE source_name = 'failure-modes-source'"
    ).fetchone()[0]
    assert count == n, f"expected {n} bronze_records rows, got {count}"
