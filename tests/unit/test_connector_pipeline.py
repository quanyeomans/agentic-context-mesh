"""IM-2 unit tests for :class:`ConnectorPipeline` corner cases.

The end-to-end happy-path / dead-letter / poison / rollback paths are
proven in ``tests/integration/test_connector_pipeline.py`` (marked
``@pytest.mark.integration``). This unit file covers the
per-method seams that don't require the full pipeline composition:

* :class:`BatchResult` is a frozen dataclass.
* :class:`ChunkWriter` Protocol is :func:`runtime_checkable`.
* Extract failure (extractor raises) is absorbed into dead_letter,
  same as fetch failure.
* Empty change stream is a no-op (cursor unchanged, zero counts).
"""

from __future__ import annotations

import dataclasses
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from kairix.core.connectors import StreamingBronzeStore
from kairix.core.connectors.cursor_store import CursorStore
from kairix.core.connectors.dead_letter import DeadLetterStore
from kairix.core.connectors.pipeline import BatchResult, ChunkWriter, ConnectorPipeline
from kairix.core.connectors.silver import DefaultSilverProcessor
from kairix.core.db.schema import create_schema
from kairix.core.protocols import ChangeEvent
from tests.fakes import FakeChunkWriter, FakeEntityGraphSink, FakeExtractor, FakeSourceConnector

pytestmark = pytest.mark.unit


def _open_db(tmp_path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(str(tmp_path / "kairix.db"))
    create_schema(db)
    return db


def _build_pipeline(db: sqlite3.Connection, tmp_path: Path) -> ConnectorPipeline:
    del tmp_path  # Phase 7: streaming bronze writes no files
    return ConnectorPipeline(
        db=db,
        bronze=StreamingBronzeStore(db),
        silver=DefaultSilverProcessor(),
        chunk_writer=FakeChunkWriter(),
        entity_graph_sink=FakeEntityGraphSink(),
        cursor_store=CursorStore(db),
        dead_letter=DeadLetterStore(db),
    )


def test_batch_result_is_frozen_dataclass() -> None:
    assert dataclasses.is_dataclass(BatchResult)
    params = BatchResult.__dataclass_params__
    assert params.frozen is True


def test_chunk_writer_protocol_is_runtime_checkable() -> None:
    """A class implementing the full Protocol surface satisfies the
    ``runtime_checkable`` ``isinstance`` check.

    ADR-036 (#459 Slice A) extended :class:`ChunkWriter` with
    :meth:`delete_by_source_uri`; the minimal stub here mirrors both
    methods so the Protocol-shape contract stays mechanically enforced.
    """

    class _Writer:
        def upsert(self, _chunks: object) -> int:
            return 0

        def delete_by_source_uri(self, _source_uri: str) -> int:
            return 0

    assert isinstance(_Writer(), ChunkWriter)


def test_extract_failure_is_absorbed_into_dead_letter(tmp_path: Path) -> None:
    """When ``extractor.extract`` raises, the item goes to dead-letter; siblings pass."""

    class _BrokenExtractor(FakeExtractor):
        def __init__(self, fail_on: str) -> None:
            super().__init__()
            self._fail_on = fail_on

        def extract(self, raw: bytes, mime: str) -> object:
            text = raw.decode("utf-8", errors="replace")
            if self._fail_on in text:
                raise RuntimeError(f"extract: simulated failure on {self._fail_on!r}")
            return super().extract(raw, mime)

    db = _open_db(tmp_path)
    try:
        pipeline = _build_pipeline(db, tmp_path)
        events = [
            ChangeEvent(op="modified", item_id=f"note-{i}.md", modified_at=f"2026-05-22T10:0{i}:00Z") for i in (1, 2, 3)
        ]
        content = {
            "note-1.md": b"alpha body content",
            "note-2.md": b"trigger-extract-failure body",
            "note-3.md": b"gamma body content",
        }
        connector = FakeSourceConnector(name="fake-source", events=events, content=content)
        extractor = _BrokenExtractor(fail_on="trigger-extract-failure")

        result = pipeline.run_batch(connector, extractor)

        assert result.processed == 2
        assert result.dead_lettered == 1
        # Item 2 sits in dead_letter with failure_count = 1 and the
        # 'extract: ...' prefix the pipeline records for extract failures.
        row = db.execute(
            "SELECT failure_count, last_error FROM connector_deadletter WHERE source_name = ? AND item_id = ?",
            ("fake-source", "note-2.md"),
        ).fetchone()
        assert row is not None
        assert row[0] == 1
        assert row[1].startswith("extract:")
    finally:
        db.close()


def test_empty_change_stream_is_a_no_op(tmp_path: Path) -> None:
    """No changes → no chunks written, cursor unchanged, zero counts."""
    db = _open_db(tmp_path)
    try:
        pipeline = _build_pipeline(db, tmp_path)
        connector = FakeSourceConnector(name="fake-source", events=[])
        result = pipeline.run_batch(connector, FakeExtractor())

        assert result.processed == 0
        assert result.dead_lettered == 0
        assert result.poisoned_skipped == 0

        # No cursor was written — fresh connection sees None.
        fresh = sqlite3.connect(str(tmp_path / "kairix.db"))
        try:
            assert CursorStore(fresh).read("fake-source") is None
        finally:
            fresh.close()
    finally:
        db.close()


def test_fetch_failure_records_dead_letter(tmp_path: Path) -> None:
    """When ``connector.fetch`` raises, the item lands in dead_letter (fetch path)."""
    db = _open_db(tmp_path)
    try:
        pipeline = _build_pipeline(db, tmp_path)
        events = [ChangeEvent(op="modified", item_id="boom.md", modified_at="2026-05-22T10:00:00Z")]
        connector = FakeSourceConnector(
            name="fake-source",
            events=events,
            content={},
            fail_on_fetch={"boom.md"},
        )

        result = pipeline.run_batch(connector, FakeExtractor())

        assert result.processed == 0
        assert result.dead_lettered == 1
        row = db.execute(
            "SELECT failure_count, last_error FROM connector_deadletter WHERE source_name = ? AND item_id = ?",
            ("fake-source", "boom.md"),
        ).fetchone()
        assert row is not None
        assert row[0] == 1
        assert row[1].startswith("fetch:")
    finally:
        db.close()


def test_poisoned_item_is_skipped_before_fetch(tmp_path: Path) -> None:
    """Pre-seeded poison → pipeline skips the item without calling fetch."""
    db = _open_db(tmp_path)
    try:
        pipeline = _build_pipeline(db, tmp_path)
        # Seed three pre-existing failures so the item is already poisoned.
        dead_letter = DeadLetterStore(db)
        for _ in range(3):
            dead_letter.record("fake-source", "poisoned.md", "earlier failure")
        db.commit()

        events = [
            ChangeEvent(op="modified", item_id="poisoned.md", modified_at="2026-05-22T10:00:00Z"),
        ]
        connector = FakeSourceConnector(
            name="fake-source",
            events=events,
            content={"poisoned.md": b"body that won't be fetched"},
        )

        result = pipeline.run_batch(connector, FakeExtractor())

        assert result.processed == 0
        assert result.dead_lettered == 0
        assert result.poisoned_skipped == 1
        # The pipeline skipped fetch entirely.
        assert connector.fetch_calls == []
    finally:
        db.close()


def test_batch_level_failure_rolls_back(tmp_path: Path) -> None:
    """Silver raises → run_batch rolls back and re-raises."""

    class _ExplodingSilver:
        def process(self, *_args: object, **_kwargs: object) -> object:
            raise RuntimeError("silver: unit-level rollback proof")

    db = _open_db(tmp_path)
    try:
        # Seed a pre-existing cursor so we can prove it doesn't advance.
        CursorStore(db).write("fake-source", "PRE-EXISTING")
        db.commit()

        pipeline = ConnectorPipeline(
            db=db,
            bronze=StreamingBronzeStore(db),
            silver=_ExplodingSilver(),  # type: ignore[arg-type]  # F3-rationale: synthetic Protocol-compliant stand-in for negative-path test.
            chunk_writer=FakeChunkWriter(),
            entity_graph_sink=FakeEntityGraphSink(),
            cursor_store=CursorStore(db),
            dead_letter=DeadLetterStore(db),
        )
        events = [ChangeEvent(op="modified", item_id="x.md", modified_at="2026-05-22T11:00:00Z")]
        connector = FakeSourceConnector(name="fake-source", events=events, content={"x.md": b"body"})

        with pytest.raises(RuntimeError, match="silver: unit-level rollback proof"):
            pipeline.run_batch(connector, FakeExtractor())

        # Cursor unchanged on a fresh connection — rollback took effect.
        fresh = sqlite3.connect(str(tmp_path / "kairix.db"))
        try:
            assert CursorStore(fresh).read("fake-source") == "PRE-EXISTING"
        finally:
            fresh.close()
    finally:
        db.close()


# ----------------------------------------------------------------------
# GH #336 (ADR-024 Bundle B) — _safe_quality_ok + _safe_outcome_write
# defensive fallback branches are exercised through the public pipeline
# surface via tests/integration/test_documents_media_writer.py. Per F5
# we don't import the underscore-prefixed helpers directly — instead
# the tests below drive ConnectorPipeline.run_batch with stand-in
# extractors / silvers that hit each fallback path.
# ----------------------------------------------------------------------


class _ExtractorMissingQuality:
    """Extractor stand-in with no ``quality_ok`` attribute — exercises the public fallback."""

    name = "no-quality"
    version = "v0"

    def can_extract(self, _mime: str, _magic: bytes) -> bool:
        return True

    def extract(self, raw: bytes, _mime: str) -> Any:
        from kairix.core.protocols import DocMetadata, ExtractedDocument

        return ExtractedDocument(
            markdown=raw.decode("utf-8", errors="replace") or "body",
            pages=(),
            images=(),
            metadata=DocMetadata(title=None, author=None, created_date=None, language=None, page_count=None),
            confidence=1.0,
        )

    # No ``quality_ok`` -> _safe_quality_ok's "method missing" branch
    # fires when the pipeline calls it. The expected status the
    # orchestrator surfaces is ``ok`` (the helper defaults to True).

    def metadata_for(self, _raw: bytes, _mime: str) -> Any:
        from kairix.core.protocols import SourceMetadata

        return SourceMetadata()


class _ExtractorRaisingQuality:
    """Extractor stand-in whose ``quality_ok`` raises — exercises the public swallow fallback."""

    name = "raise-quality"
    version = "v0"

    def can_extract(self, _mime: str, _magic: bytes) -> bool:
        return True

    def extract(self, raw: bytes, _mime: str) -> Any:
        from kairix.core.protocols import DocMetadata, ExtractedDocument

        return ExtractedDocument(
            markdown=raw.decode("utf-8", errors="replace") or "body",
            pages=(),
            images=(),
            metadata=DocMetadata(title=None, author=None, created_date=None, language=None, page_count=None),
            confidence=1.0,
        )

    def quality_ok(self, _doc: object) -> bool:
        raise RuntimeError("scripted quality_ok failure")

    def metadata_for(self, _raw: bytes, _mime: str) -> Any:
        from kairix.core.protocols import SourceMetadata

        return SourceMetadata()


def _run_one_item_through_factory_pipeline(tmp_path: Path, extractor: object) -> str | None:
    """Drive one happy-path item through the production factory pipeline.

    Returns the resulting ``documents_media.extraction_status`` so the
    caller can assert the safe-quality-ok fallback produces ``ok``
    (the documented default when the helper's exception/missing-method
    fallback fires).
    """
    from kairix.core import factory
    from kairix.core.db.schema import create_schema

    db = sqlite3.connect(str(tmp_path / "fallback.sqlite"))
    create_schema(db)
    pipeline = factory.build_connector_pipeline(
        db=db,
        collection="safe-quality-fallback",
        chunk_writer=FakeChunkWriter(),
        entity_graph_sink=FakeEntityGraphSink(),
    )
    connector = FakeSourceConnector(
        name="fallback-source",
        events=[ChangeEvent(op="modified", item_id="doc.md", modified_at="2026-05-28T10:00:00Z")],
        content={"doc.md": b"body text"},
        cursor_token="fallback-cursor",
    )
    pipeline.run_batch(connector, extractor)  # type: ignore[arg-type]  # F3-rationale: synthetic stand-in satisfies the runtime-checkable Extractor Protocol
    row = db.execute("SELECT extraction_status FROM documents_media LIMIT 1").fetchone()
    db.close()
    return None if row is None else str(row[0])


def test_extractor_missing_quality_method_yields_ok_status_via_public_pipeline(tmp_path: Path) -> None:
    """Extractor with no quality_ok method -> safe-quality-ok fallback returns True -> status='ok'."""
    status = _run_one_item_through_factory_pipeline(tmp_path, _ExtractorMissingQuality())
    assert status == "ok"


def test_extractor_quality_method_raising_yields_ok_status_via_public_pipeline(tmp_path: Path) -> None:
    """Extractor whose quality_ok raises -> safe-quality-ok swallows -> status='ok'."""
    status = _run_one_item_through_factory_pipeline(tmp_path, _ExtractorRaisingQuality())
    assert status == "ok"
