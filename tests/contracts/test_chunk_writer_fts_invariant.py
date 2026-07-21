"""Contract test — every chunk written through the connector framework
MUST also land in ``documents_fts`` so BM25 retrieval can find it.

This invariant was missed in the IM-6 cutover: the connector framework's
chunk writer populated ``documents`` + ``content`` + ``content_vectors``
but skipped FTS5, leaving 68,814 chunks in the ``obsidian`` collection
invisible to BM25. The hybrid ranker silently degraded to vector-only.

Pin the invariant via the public ``build_connector_pipeline`` factory so
any future ChunkWriter implementation (Wave C ``CollectionRouter`` will
own the canonical write path; Wave F chunker plugins write through it)
inherits the requirement.

Sabotage-proven: remove the FTS5 write from the pipeline's chunk-writer
and this test fails; restore and it passes.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from kairix.core.db.schema import create_schema
from kairix.core.factory import build_connector_pipeline
from kairix.core.protocols import ChangeEvent, DocMetadata, ExtractedDocument, Page
from tests.fakes import FakeExtractor, FakeSourceConnector


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@pytest.fixture
def db_with_bronze(tmp_path: Path) -> tuple[sqlite3.Connection, Path]:
    db = sqlite3.connect(":memory:")
    create_schema(db, dims=4)
    bronze = tmp_path / "bronze"
    bronze.mkdir()
    return db, bronze


def _run_one_chunk(db: sqlite3.Connection, bronze: Path, collection: str, item_id: str, body: bytes) -> None:
    fake = FakeSourceConnector(
        name=collection,
        events=[ChangeEvent(op="created", item_id=item_id, modified_at=_now())],
        content={item_id: body},
    )
    pipeline = build_connector_pipeline(db=db, bronze_root=bronze, collection=collection)
    pipeline.run_batch(fake, FakeExtractor())
    db.commit()


class _PagedExtractor:
    """Public-protocol fake that emits a configured set of page objects."""

    name = "paged-fake"
    version = "0.0.0"

    def __init__(self, pages: tuple[Page, ...]) -> None:
        self._pages = pages

    def can_extract(self, _mime: str, _magic_bytes: bytes) -> bool:
        return True

    def extract(self, _raw: bytes, _mime: str) -> ExtractedDocument:
        markdown = "\n\n".join(page.text for page in self._pages)
        return ExtractedDocument(
            markdown=markdown,
            pages=self._pages,
            images=(),
            metadata=DocMetadata(
                title=None,
                author=None,
                created_date=None,
                language=None,
                page_count=len(self._pages),
            ),
            confidence=1.0,
        )

    def quality_ok(self, _doc: ExtractedDocument) -> bool:
        return True

    def metadata_for(self, _raw: bytes, _mime: str) -> object:
        from kairix.core.protocols import SourceMetadata

        return SourceMetadata()


def _run_paged_source(
    db: sqlite3.Connection,
    collection: str,
    item_id: str,
    pages: tuple[Page, ...],
) -> None:
    connector = FakeSourceConnector(
        name=collection,
        events=[ChangeEvent(op="modified", item_id=item_id, modified_at=_now())],
        content={item_id: b"source bytes"},
        mime_overrides={item_id: "application/vnd.openxmlformats-officedocument.presentationml.presentation"},
    )
    pipeline = build_connector_pipeline(db=db, collection=collection)
    pipeline.run_batch(connector, _PagedExtractor(pages))
    db.commit()


@pytest.mark.contract
def test_chunk_writer_populates_documents_fts(db_with_bronze: tuple[sqlite3.Connection, Path]) -> None:
    """The canonical invariant — every chunk written must appear in
    documents_fts so BM25 retrieval finds it."""
    db, bronze = db_with_bronze
    _run_one_chunk(db, bronze, "obsidian", "note-a.md", b"kairix architecture is layered")
    _run_one_chunk(db, bronze, "obsidian", "note-b.md", b"vector retrieval uses usearch index")

    fts_count = db.execute(
        "SELECT COUNT(*) FROM documents d JOIN documents_fts fts ON fts.rowid = d.id WHERE d.collection = 'obsidian'"
    ).fetchone()[0]
    assert fts_count == 2, f"expected 2 FTS rows for obsidian collection, got {fts_count}"


@pytest.mark.contract
def test_chunk_writer_fts_text_is_searchable(db_with_bronze: tuple[sqlite3.Connection, Path]) -> None:
    """Beyond presence — the FTS5 index must be queryable by content."""
    db, bronze = db_with_bronze
    _run_one_chunk(db, bronze, "obsidian", "note.md", b"the layered architecture of kairix")
    _run_one_chunk(db, bronze, "obsidian", "recipe.md", b"unrelated content about cooking")

    matches = list(
        db.execute(
            "SELECT d.path FROM documents d JOIN documents_fts fts ON fts.rowid = d.id "
            "WHERE documents_fts MATCH ? AND d.collection = 'obsidian'",
            ("architecture",),
        )
    )
    assert len(matches) == 1
    assert "note.md" in matches[0][0]


@pytest.mark.contract
def test_chunk_writer_fts_upsert_replaces_old_text(db_with_bronze: tuple[sqlite3.Connection, Path]) -> None:
    """When a chunk at the same (collection, item) is re-ingested with new
    body, the FTS row must reflect the new text — otherwise stale content
    keeps matching."""
    db, bronze = db_with_bronze
    _run_one_chunk(db, bronze, "obsidian", "note.md", b"old content")
    _run_one_chunk(db, bronze, "obsidian", "note.md", b"new content")

    old_match = db.execute(
        "SELECT COUNT(*) FROM documents_fts WHERE documents_fts MATCH ?",
        ("old",),
    ).fetchone()[0]
    assert old_match == 0, "stale FTS row found — replacement did not clean up"

    new_match = db.execute(
        "SELECT COUNT(*) FROM documents_fts WHERE documents_fts MATCH ?",
        ("new",),
    ).fetchone()[0]
    assert new_match == 1, "FTS row not updated to new text"


@pytest.mark.contract
def test_chunk_writer_fts_count_matches_documents_count(db_with_bronze: tuple[sqlite3.Connection, Path]) -> None:
    """For every active document the connector wrote, there is exactly
    one corresponding FTS row. This pins the 1:1 invariant — the IM-6
    gap was 68,814 documents AND 0 FTS rows; the writer must close
    that gap by construction."""
    db, bronze = db_with_bronze
    for i in range(5):
        _run_one_chunk(db, bronze, "obsidian", f"f{i}.md", f"chunk text {i}".encode())

    docs_count = db.execute("SELECT COUNT(*) FROM documents WHERE collection = 'obsidian' AND active = 1").fetchone()[0]
    fts_count = db.execute(
        "SELECT COUNT(*) FROM documents d JOIN documents_fts fts ON fts.rowid = d.id WHERE d.collection = 'obsidian'"
    ).fetchone()[0]
    assert docs_count == fts_count == 5, (
        f"FTS-vs-documents mismatch — documents={docs_count} FTS={fts_count} "
        f"(the IM-6 cutover failure mode: write to docs without writing to FTS)"
    )


@pytest.mark.contract
def test_chunk_writer_rewrite_removes_obsolete_source_chunks(
    db_with_bronze: tuple[sqlite3.Connection, Path],
) -> None:
    """Re-indexing one source URI with a different chunk shape must not leave
    stale chunk rows behind.

    This pins the SharePoint extractor-chain recovery case: an old flat
    MarkItDown ingest may have emitted many ``<source_uri>#<seq>`` chunks,
    while a later page-aware PPTX/PDF extractor can emit fewer chunks with
    ``source_page`` populated. The rewrite must leave only the new chunks
    and matching FTS rows.
    """
    db, _ = db_with_bronze
    item_id = "deck.pptx"
    source_uri = "sharepoint://item/deck.pptx"

    _run_paged_source(
        db,
        "sharepoint",
        item_id,
        (
            Page(page_number=1, text="obsolete page one", has_images=False),
            Page(page_number=2, text="obsolete page two", has_images=False),
            Page(page_number=3, text="obsolete page three", has_images=False),
        ),
    )
    _run_paged_source(
        db,
        "sharepoint",
        item_id,
        (Page(page_number=7, text="current page-aware chunk", has_images=False),),
    )

    docs = db.execute(
        "SELECT path, source_page, hash FROM documents WHERE collection = ? AND source_uri = ? ORDER BY path",
        ("sharepoint", source_uri),
    ).fetchall()
    assert [(row[0], row[1]) for row in docs] == [(f"{source_uri}#0", 7)]

    pages = db.execute("SELECT page_number FROM document_pages ORDER BY page_number").fetchall()
    assert pages == [(7,)]

    old_fts_count = db.execute(
        "SELECT COUNT(*) FROM documents_fts WHERE documents_fts MATCH ?",
        ("obsolete",),
    ).fetchone()[0]
    assert old_fts_count == 0

    new_fts_count = db.execute(
        "SELECT COUNT(*) FROM documents_fts WHERE documents_fts MATCH ?",
        ("current",),
    ).fetchone()[0]
    assert new_fts_count == 1

    vector_count = db.execute("SELECT COUNT(*) FROM content_vectors").fetchone()[0]
    assert vector_count == 1
