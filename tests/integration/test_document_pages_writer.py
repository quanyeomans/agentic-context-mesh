"""GH #338 — document_pages writer wires through the connector pipeline.

ADR-024 F70 paydown: page-bearing extractors (PDF / PPTX / DOCX)
populate :attr:`ExtractedDocument.pages`; Silver writes one
``document_pages`` row per Page so downstream retrieval can cite
back to a specific page / slide / sheet.

The integration test drives the real
:class:`~kairix.core.connectors.pipeline.ConnectorPipeline` through
:func:`kairix.core.factory.build_connector_pipeline` (F47-compliant)
with a paged-extractor stand-in that emits 3 pages, then asserts
3 rows land with monotonic ``page_number`` + non-empty text.

A second scenario asserts the non-paged path is a clean no-op —
the markdown extractor produces zero pages and zero
``document_pages`` rows; the documents_media row still writes so
analytics observability isn't affected.

Sabotage proofs: comment out the writer call in
``DefaultSilverProcessor._write_documents_media`` (the per-page
branch) → the integration test fails with a concrete row-count
mismatch.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from kairix.core import factory
from kairix.core.db.schema import create_schema
from kairix.core.protocols import (
    ChangeEvent,
    DocMetadata,
    ExtractedDocument,
    Page,
    SourceMetadata,
)
from tests.fakes import FakeChunkWriter, FakeEntityGraphSink, FakeSourceConnector

pytestmark = pytest.mark.integration


class _PagedExtractor:
    """Extractor stand-in that emits N pages — exercises the per-page write path."""

    name: str = "paged-fake-extractor"
    version: str = "v0.test"

    def __init__(self, *, n_pages: int = 3) -> None:
        self._n_pages = n_pages

    def can_extract(self, _mime: str, _magic: bytes) -> bool:
        return True

    def extract(self, raw: bytes, _mime: str) -> ExtractedDocument:
        body = raw.decode("utf-8", errors="replace")
        pages = tuple(
            Page(
                page_number=i + 1,
                text=f"page {i + 1} text from body: {body[:32]}",
                has_images=(i % 2 == 1),  # alternating — exercises both has_images branches
            )
            for i in range(self._n_pages)
        )
        return ExtractedDocument(
            markdown=body,
            pages=pages,
            images=(),
            metadata=DocMetadata(title=None, author=None, created_date=None, language=None, page_count=None),
            confidence=0.9,
        )

    def quality_ok(self, _doc: ExtractedDocument) -> bool:
        return True

    def metadata_for(self, _raw: bytes, _mime: str) -> SourceMetadata:
        return SourceMetadata()


class _NonPagedExtractor:
    """Markdown-style extractor — emits zero pages. Exercises the no-op branch."""

    name: str = "non-paged-fake-extractor"
    version: str = "v0.test"

    def can_extract(self, _mime: str, _magic: bytes) -> bool:
        return True

    def extract(self, raw: bytes, _mime: str) -> ExtractedDocument:
        body = raw.decode("utf-8", errors="replace")
        return ExtractedDocument(
            markdown=body,
            pages=(),
            images=(),
            metadata=DocMetadata(title=None, author=None, created_date=None, language=None, page_count=None),
            confidence=0.9,
        )

    def quality_ok(self, _doc: ExtractedDocument) -> bool:
        return True

    def metadata_for(self, _raw: bytes, _mime: str) -> SourceMetadata:
        return SourceMetadata()


def _build_pipeline(db: sqlite3.Connection, chunk_writer: FakeChunkWriter) -> Any:
    return factory.build_connector_pipeline(
        db=db,
        collection="document-pages-writer-test",
        chunk_writer=chunk_writer,
        entity_graph_sink=FakeEntityGraphSink(),
    )


def _fetch_pages_rows(db: sqlite3.Connection) -> list[dict[str, Any]]:
    cursor = db.execute(
        "SELECT hash, page_number, extracted_text, has_images, image_descriptions "
        "FROM document_pages ORDER BY hash, page_number"
    )
    cols = [c[0] for c in cursor.description]
    return [dict(zip(cols, row, strict=True)) for row in cursor.fetchall()]


def test_paged_extract_writes_one_document_pages_row_per_page(tmp_path: Path) -> None:
    """A paged extractor emits 3 Pages → 3 document_pages rows monotonic by page_number."""
    db_path = tmp_path / "paged.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    chunk_writer = FakeChunkWriter()
    pipeline = _build_pipeline(db, chunk_writer)
    body = b"<binary pdf-ish payload>"
    connector = FakeSourceConnector(
        name="paged-source",
        events=[ChangeEvent(op="modified", item_id="report.pdf", modified_at="2026-05-28T10:00:00Z")],
        content={"report.pdf": body},
        cursor_token="paged-cursor-1",
    )

    result = pipeline.run_batch(connector, _PagedExtractor(n_pages=3))
    assert result.processed == 1

    rows = _fetch_pages_rows(db)
    assert len(rows) == 3, f"expected 3 document_pages rows for a 3-page extract; got {rows!r}"

    # Monotonic page numbers starting at 1, non-empty text, alternating has_images.
    assert [r["page_number"] for r in rows] == [1, 2, 3]
    assert all(r["extracted_text"] and len(r["extracted_text"]) > 0 for r in rows)
    assert [r["has_images"] for r in rows] == [0, 1, 0]
    # image_descriptions is forward-armed (vision extractor) — NULL today.
    assert all(r["image_descriptions"] is None for r in rows)
    # All three rows share the same content_hash (one document, three pages).
    hashes = {r["hash"] for r in rows}
    assert len(hashes) == 1, f"expected all 3 pages to share one content_hash; got {hashes!r}"


def test_non_paged_extract_writes_no_document_pages_rows(tmp_path: Path) -> None:
    """A markdown-style extractor emits zero Pages → zero document_pages rows.

    Documents_media still writes (the document was successfully extracted),
    so this asserts the page-writer no-ops cleanly without affecting the
    per-document analytics row.
    """
    db_path = tmp_path / "non-paged.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    chunk_writer = FakeChunkWriter()
    pipeline = _build_pipeline(db, chunk_writer)
    body = b"# heading\n\nbody paragraph\n"
    connector = FakeSourceConnector(
        name="non-paged-source",
        events=[ChangeEvent(op="modified", item_id="note.md", modified_at="2026-05-28T10:00:00Z")],
        content={"note.md": body},
        cursor_token="non-paged-cursor-1",
    )

    result = pipeline.run_batch(connector, _NonPagedExtractor())
    assert result.processed == 1

    rows = _fetch_pages_rows(db)
    assert rows == [], f"expected zero document_pages rows for non-paged extract; got {rows!r}"

    # documents_media still wrote — the non-paged path is a clean no-op
    # for document_pages WITHOUT skipping the per-document row.
    media = db.execute("SELECT COUNT(*) FROM documents_media").fetchone()[0]
    assert media == 1, f"documents_media row must still write; got count={media}"


def test_re_ingest_replaces_pages_idempotently(tmp_path: Path) -> None:
    """INSERT OR REPLACE — re-ingesting the same document updates rows, doesn't accumulate.

    Sabotage proof for the writer's idempotency: change the writer to
    plain INSERT (no OR REPLACE), re-run this test, see it fail with
    UNIQUE constraint violation on (hash, page_number).
    """
    db_path = tmp_path / "reingest.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    chunk_writer = FakeChunkWriter()
    pipeline = _build_pipeline(db, chunk_writer)
    body = b"<binary pdf-ish payload>"
    connector = FakeSourceConnector(
        name="reingest-source",
        events=[ChangeEvent(op="modified", item_id="report.pdf", modified_at="2026-05-28T10:00:00Z")],
        content={"report.pdf": body},
        cursor_token="reingest-cursor-1",
    )

    pipeline.run_batch(connector, _PagedExtractor(n_pages=3))
    # Re-ingest the same item — the writer must replace, not append.
    connector_2 = FakeSourceConnector(
        name="reingest-source",
        events=[ChangeEvent(op="modified", item_id="report.pdf", modified_at="2026-05-28T11:00:00Z")],
        content={"report.pdf": body},
        cursor_token="reingest-cursor-2",
    )
    pipeline.run_batch(connector_2, _PagedExtractor(n_pages=3))

    rows = _fetch_pages_rows(db)
    assert len(rows) == 3, f"re-ingest must replace pages, not accumulate; expected 3 rows, got {len(rows)} — {rows!r}"
