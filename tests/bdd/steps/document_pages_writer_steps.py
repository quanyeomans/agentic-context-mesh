"""Step impls for document_pages_writer.feature (GH #338 / ADR-024 F70 paydown).

Drives the real :class:`kairix.core.connectors.pipeline.ConnectorPipeline`
via :func:`kairix.core.factory.build_connector_pipeline` (F46 / F47).

Per-page row writes happen in
:class:`kairix.core.connectors.silver.SqliteDocumentPagesWriter`, wired
into ``DefaultSilverProcessor`` by the production factory when no
``silver=`` override is supplied.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, parsers, then, when

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


class _BddPagedExtractor:
    name: str = "paged-bdd-extractor"
    version: str = "v0.bdd"

    def __init__(self, *, n_pages: int = 3) -> None:
        self._n_pages = n_pages

    def can_extract(self, _mime: str, _magic: bytes) -> bool:
        return True

    def extract(self, raw: bytes, _mime: str) -> ExtractedDocument:
        body = raw.decode("utf-8", errors="replace") or "paged-bdd"
        pages = tuple(
            Page(
                page_number=i + 1,
                text=f"page {i + 1} text from body: {body[:32]}",
                has_images=(i % 2 == 1),
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


class _BddNonPagedExtractor:
    name: str = "non-paged-bdd-extractor"
    version: str = "v0.bdd"

    def can_extract(self, _mime: str, _magic: bytes) -> bool:
        return True

    def extract(self, raw: bytes, _mime: str) -> ExtractedDocument:
        body = raw.decode("utf-8", errors="replace") or "non-paged-bdd"
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


@dataclass
class _ScenarioState:
    db: sqlite3.Connection
    pipeline: Any
    connector: FakeSourceConnector
    extractor: Any


@pytest.fixture
def page_state(tmp_path: Path) -> _ScenarioState:
    db = sqlite3.connect(str(tmp_path / "pages.sqlite"))
    create_schema(db)
    pipeline = factory.build_connector_pipeline(
        db=db,
        collection="bdd-document-pages",
        chunk_writer=FakeChunkWriter(),
        entity_graph_sink=FakeEntityGraphSink(),
    )
    # Placeholder — given-steps overwrite these.
    state = _ScenarioState(
        db=db,
        pipeline=pipeline,
        connector=FakeSourceConnector(name="placeholder"),
        extractor=_BddPagedExtractor(),
    )
    yield state
    db.close()


@given(parsers.parse('a connector "{name}" with one binary-pdf change event'))
def _paged_source(page_state: _ScenarioState, name: str) -> None:
    page_state.connector = FakeSourceConnector(
        name=name,
        events=[ChangeEvent(op="modified", item_id="report.pdf", modified_at="2026-05-28T10:00:00Z")],
        content={"report.pdf": b"<binary pdf-ish payload>"},
        cursor_token=f"{name}-cursor-1",
    )


@given(parsers.parse('a connector "{name}" with one non-paged change event'))
def _markdown_source(page_state: _ScenarioState, name: str) -> None:
    page_state.connector = FakeSourceConnector(
        name=name,
        events=[ChangeEvent(op="modified", item_id="note.md", modified_at="2026-05-28T10:00:00Z")],
        content={"note.md": b"# heading\n\nbody paragraph\n"},
        cursor_token=f"{name}-cursor-1",
    )


@given(parsers.parse('a connector "{name}" that runs the same item twice'))
def _reingest_source(page_state: _ScenarioState, name: str) -> None:
    page_state.connector = FakeSourceConnector(
        name=name,
        events=[ChangeEvent(op="modified", item_id="report.pdf", modified_at="2026-05-28T10:00:00Z")],
        content={"report.pdf": b"<binary pdf-ish payload>"},
        cursor_token=f"{name}-cursor-1",
    )


@given(parsers.parse("the configured extractor emits {n:d} pages with text and alternating has_images"))
def _emit_n_pages_alternating(page_state: _ScenarioState, n: int) -> None:
    page_state.extractor = _BddPagedExtractor(n_pages=n)


@given(parsers.parse("the configured extractor emits zero pages"))
def _emit_zero_pages(page_state: _ScenarioState) -> None:
    page_state.extractor = _BddNonPagedExtractor()


@given(parsers.parse("the configured extractor emits {n:d} pages each time"))
def _emit_n_pages(page_state: _ScenarioState, n: int) -> None:
    page_state.extractor = _BddPagedExtractor(n_pages=n)


@when(parsers.parse('the operator runs one pipeline batch for the pages source "{name}"'))
def _run_one_batch(page_state: _ScenarioState, name: str) -> None:
    del name  # name is the connector name; the connector is already bound on page_state
    page_state.pipeline.run_batch(page_state.connector, page_state.extractor)


@when("the operator runs two consecutive pipeline batches")
def _run_two_batches(page_state: _ScenarioState) -> None:
    page_state.pipeline.run_batch(page_state.connector, page_state.extractor)
    # Re-construct a fresh connector for the second batch so the events queue isn't exhausted.
    page_state.connector = FakeSourceConnector(
        name=page_state.connector.name,
        events=[ChangeEvent(op="modified", item_id="report.pdf", modified_at="2026-05-28T11:00:00Z")],
        content={"report.pdf": b"<binary pdf-ish payload>"},
        cursor_token=f"{page_state.connector.name}-cursor-2",
    )
    page_state.pipeline.run_batch(page_state.connector, page_state.extractor)


@then(parsers.parse("{n:d} document_pages rows exist for that document"))
def _assert_n_rows(page_state: _ScenarioState, n: int) -> None:
    count = page_state.db.execute("SELECT COUNT(*) FROM document_pages").fetchone()[0]
    assert count == n, f"expected {n} document_pages rows, got {count}"


@then(parsers.parse("{n:d} document_pages rows exist"))
def _assert_n_rows_total(page_state: _ScenarioState, n: int) -> None:
    count = page_state.db.execute("SELECT COUNT(*) FROM document_pages").fetchone()[0]
    assert count == n, f"expected {n} document_pages rows total, got {count}"


@then("the page numbers are 1, 2, 3 in ascending order")
def _assert_page_numbers(page_state: _ScenarioState) -> None:
    page_numbers = [
        r[0] for r in page_state.db.execute("SELECT page_number FROM document_pages ORDER BY page_number").fetchall()
    ]
    assert page_numbers == [1, 2, 3], f"expected page_numbers=[1,2,3], got {page_numbers!r}"


@then("every row has non-empty extracted_text")
def _assert_non_empty_text(page_state: _ScenarioState) -> None:
    rows = page_state.db.execute("SELECT extracted_text FROM document_pages").fetchall()
    assert rows, "no document_pages rows to inspect"
    assert all(r[0] and len(r[0]) > 0 for r in rows), f"empty extracted_text found: {rows!r}"


@then("image_descriptions is NULL on every row")
def _assert_image_descriptions_null(page_state: _ScenarioState) -> None:
    rows = page_state.db.execute("SELECT image_descriptions FROM document_pages").fetchall()
    assert rows, "no document_pages rows to inspect"
    assert all(r[0] is None for r in rows), f"image_descriptions populated unexpectedly: {rows!r}"


@then("the documents_media row still wrote (per-document analytics unaffected)")
def _assert_documents_media_wrote(page_state: _ScenarioState) -> None:
    count = page_state.db.execute("SELECT COUNT(*) FROM documents_media").fetchone()[0]
    assert count == 1, f"documents_media must write even when extractor emits no pages; got {count}"


@then("document_pages still has exactly 3 rows (INSERT OR REPLACE, not append)")
def _assert_no_accumulation(page_state: _ScenarioState) -> None:
    count = page_state.db.execute("SELECT COUNT(*) FROM document_pages").fetchone()[0]
    assert count == 3, f"re-ingest must replace pages, not accumulate; expected 3 rows, got {count}"


__all__ = ["page_state"]  # fixture name re-exported for the test binding
