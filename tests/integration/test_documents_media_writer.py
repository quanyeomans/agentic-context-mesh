"""GH #336 — documents_media writer wires through the connector pipeline.

ADR-024 Bundle B: the documents_media row writes one entry per
processed document carrying extractor identity, extraction status,
page count, and ADR-021-merged envelope metadata. The integration
test drives the real :class:`~kairix.core.connectors.pipeline.ConnectorPipeline`
through ``kairix.core.factory.build_connector_pipeline`` (F47-compliant)
and asserts directly against the ``documents_media`` table after one
batch.

Three scenarios:

1. **happy_path** — a single passthrough markdown ingestion lands a
   row with ``extraction_status='ok'``, ``extractor_name`` +
   ``extractor_version`` from the resolved Extractor.
2. **failure_path** — an extractor that raises lands a row with
   ``extraction_status='failed'`` AND the item lands in dead-letter.
   Sibling items still process.
3. **unsupported_path** — an extractor whose ``quality_ok`` returns
   False lands a row with ``extraction_status='unsupported'``; the
   chunks still land but the dashboard signals the re-extract
   escalation surface.

Sabotage proofs (per ``feedback_sabotage_must_be_executed``): mutate
the INSERT statement (drop a field), re-run the test, assert it
fails with a concrete column mismatch, restore.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from kairix.core import factory
from kairix.core.db.schema import create_schema
from kairix.core.protocols import ChangeEvent, DocMetadata, ExtractedDocument
from tests.fakes import FakeChunkWriter, FakeEntityGraphSink, FakeExtractor, FakeSourceConnector

pytestmark = pytest.mark.integration


class _RaisingExtractor:
    """Extractor stand-in that raises on ``extract`` — exercises the failed path."""

    name: str = "raising-extractor"
    version: str = "v0.test"

    def can_extract(self, _mime: str, _magic: bytes) -> bool:
        return True

    def extract(self, _raw: bytes, _mime: str) -> Any:
        raise RuntimeError("scripted failure: corrupt PDF")

    def quality_ok(self, _doc: Any) -> bool:  # pragma: no cover — extract raises first
        return False

    def metadata_for(self, _raw: bytes, _mime: str) -> Any:
        from kairix.core.protocols import SourceMetadata

        return SourceMetadata()


class _UnsupportedQualityExtractor:
    """Extractor that returns a doc but reports ``quality_ok=False``.

    Mirrors the ADR-024 §F70 ``unsupported`` semantic — the chain
    member produced an :class:`ExtractedDocument` but the orchestrator
    routes the row to ``extraction_status='unsupported'`` so downstream
    re-extract escalation is observable on the dashboard.
    """

    name: str = "unsupported-extractor"
    version: str = "v0.test"

    def can_extract(self, _mime: str, _magic: bytes) -> bool:
        return True

    def extract(self, raw: bytes, _mime: str) -> ExtractedDocument:
        # Return a body that's small but non-empty so silver still
        # emits chunks — the unsupported status surfaces orthogonal to
        # whether chunks landed (mirrors real escalation: a flat
        # text extract from a PDF is "supported but low quality").
        text = raw.decode("utf-8", errors="replace") or "unsupported-doc"
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


def _build_pipeline(db: sqlite3.Connection, chunk_writer: FakeChunkWriter) -> Any:
    return factory.build_connector_pipeline(
        db=db,
        collection="documents-media-writer-test",
        chunk_writer=chunk_writer,
        entity_graph_sink=FakeEntityGraphSink(),
    )


def _fetch_media_rows(db: sqlite3.Connection) -> list[dict[str, Any]]:
    cursor = db.execute(
        "SELECT hash, path, format, size_bytes, page_count, extraction_status, "
        "extractor_name, extractor_version FROM documents_media ORDER BY hash"
    )
    cols = [c[0] for c in cursor.description]
    return [dict(zip(cols, row, strict=True)) for row in cursor.fetchall()]


def test_happy_path_writes_documents_media_row_with_extractor_identity(tmp_path: Path) -> None:
    """One passthrough ingest -> one documents_media row with ok status + extractor identity."""
    db_path = tmp_path / "happy.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    chunk_writer = FakeChunkWriter()
    pipeline = _build_pipeline(db, chunk_writer)
    body = ("Body text. " * 30).encode("utf-8")
    connector = FakeSourceConnector(
        name="happy-source",
        events=[ChangeEvent(op="modified", item_id="doc-1.md", modified_at="2026-05-28T10:00:00Z")],
        content={"doc-1.md": body},
        cursor_token="happy-cursor-1",
    )

    result = pipeline.run_batch(connector, FakeExtractor())

    assert result.processed == 1
    rows = _fetch_media_rows(db)
    assert len(rows) == 1, f"expected exactly one documents_media row; got {rows!r}"
    row = rows[0]
    assert row["extraction_status"] == "ok"
    assert row["extractor_name"] == "fake-extractor"
    assert row["extractor_version"] == "0.0.0"
    assert row["path"] == "doc-1.md"
    assert row["format"] == "text/markdown"
    assert row["size_bytes"] is not None and row["size_bytes"] > 0


def test_failure_path_writes_documents_media_row_with_failed_status(tmp_path: Path) -> None:
    """An extractor that raises lands a row with extraction_status='failed' AND dead-letters the item."""
    db_path = tmp_path / "failed.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    chunk_writer = FakeChunkWriter()
    pipeline = _build_pipeline(db, chunk_writer)
    body = b"%PDF-1.4 corrupt..."
    connector = FakeSourceConnector(
        name="failed-source",
        events=[ChangeEvent(op="modified", item_id="corrupt-doc.pdf", modified_at="2026-05-28T10:00:00Z")],
        content={"corrupt-doc.pdf": body},
        cursor_token="failed-cursor-1",
    )

    result = pipeline.run_batch(connector, _RaisingExtractor())

    # The extractor raised; the item was dead-lettered, not processed.
    assert result.dead_lettered == 1
    assert result.processed == 0
    # But documents_media STILL records the failed extraction so the
    # dashboard can see it.
    rows = _fetch_media_rows(db)
    assert len(rows) == 1, f"expected one documents_media row for the failed extraction; got {rows!r}"
    row = rows[0]
    assert row["extraction_status"] == "failed"
    assert row["extractor_name"] == "raising-extractor"
    assert row["extractor_version"] == "v0.test"
    assert row["path"] == "corrupt-doc.pdf"


def test_unsupported_quality_lands_unsupported_status(tmp_path: Path) -> None:
    """quality_ok=False routes through silver but tags documents_media as 'unsupported'."""
    db_path = tmp_path / "unsupported.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    chunk_writer = FakeChunkWriter()
    pipeline = _build_pipeline(db, chunk_writer)
    body = b"low-quality body text"
    connector = FakeSourceConnector(
        name="unsupported-source",
        events=[ChangeEvent(op="modified", item_id="video.mp4", modified_at="2026-05-28T10:00:00Z")],
        content={"video.mp4": body},
        cursor_token="unsupported-cursor-1",
    )

    result = pipeline.run_batch(connector, _UnsupportedQualityExtractor())

    # The item still processed (silver ran, chunks landed) but the
    # status tells the dashboard the extraction was low-quality.
    assert result.processed == 1
    rows = _fetch_media_rows(db)
    assert len(rows) == 1, f"expected one documents_media row for the unsupported extraction; got {rows!r}"
    row = rows[0]
    assert row["extraction_status"] == "unsupported"
    assert row["extractor_name"] == "unsupported-extractor"
    assert row["extractor_version"] == "v0.test"
