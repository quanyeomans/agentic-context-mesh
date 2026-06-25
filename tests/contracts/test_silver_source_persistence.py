"""Contract: Silver persists the per-document source markdown to
``silver_source`` at ingest so the re-chunk sweep (ADR-028 Wave F.4) can
re-chunk from the original text without re-fetching from the remote
connector. Keyed by the raw-bytes content_hash; silent no-op when no
writer is wired or the doc has no content_hash / no markdown.
"""

from __future__ import annotations

import sqlite3

import pytest

from kairix.core.connectors.silver import (
    DefaultSilverProcessor,
    SqliteDocumentsMediaWriter,
    SqliteSilverSourceWriter,
)
from kairix.core.db.schema import create_schema
from kairix.core.protocols import BronzeRef, DocMetadata, ExtractedDocument

pytestmark = pytest.mark.contract

_MARKDOWN = "# Title\n\nFirst paragraph body.\n\nSecond paragraph body."


def _extracted(markdown: str = _MARKDOWN) -> ExtractedDocument:
    return ExtractedDocument(
        markdown=markdown,
        pages=(),
        images=(),
        metadata=DocMetadata(title="Title", author=None, created_date=None, language=None, page_count=None),
        confidence=1.0,
    )


def _bronze(content_hash: str | None = "rawhash-abc") -> BronzeRef:
    return BronzeRef(
        source_name="sharepoint",
        item_id="doc-1",
        raw_path=None,
        mime="text/markdown",
        fetched_at="2026-06-25T00:00:00Z",
        content_hash=content_hash,
    )


def _process(silver: DefaultSilverProcessor, raw: BronzeRef, extracted: ExtractedDocument) -> None:
    silver.process(
        raw=raw,
        extracted=extracted,
        source_uri="sharepoint://site/doc-1",
        source_modified_at="2026-06-25T00:00:00Z",
        sensitivity="internal",
    )


def test_ingest_persists_source_markdown_keyed_by_content_hash() -> None:
    db = sqlite3.connect(":memory:")
    create_schema(db)
    silver = DefaultSilverProcessor(
        documents_media_writer=SqliteDocumentsMediaWriter(db),
        silver_source_writer=SqliteSilverSourceWriter(db),
    )
    raw = _bronze()
    _process(silver, raw, _extracted())
    db.commit()

    row = db.execute(
        "SELECT source_uri, markdown FROM silver_source WHERE hash = ?", (raw.content_hash,)
    ).fetchone()
    assert row is not None, "silver_source row must be written at ingest"
    assert row[0] == "sharepoint://site/doc-1", "source_uri must be stored for the sweep's delete path"
    assert row[1] == _MARKDOWN


def test_no_source_writer_is_a_silent_noop() -> None:
    db = sqlite3.connect(":memory:")
    create_schema(db)
    silver = DefaultSilverProcessor(documents_media_writer=SqliteDocumentsMediaWriter(db))
    _process(silver, _bronze(), _extracted())
    db.commit()

    assert db.execute("SELECT COUNT(*) FROM silver_source").fetchone()[0] == 0


def test_no_content_hash_skips_source_write() -> None:
    db = sqlite3.connect(":memory:")
    create_schema(db)
    silver = DefaultSilverProcessor(
        documents_media_writer=SqliteDocumentsMediaWriter(db),
        silver_source_writer=SqliteSilverSourceWriter(db),
    )
    _process(silver, _bronze(content_hash=None), _extracted())
    db.commit()

    assert db.execute("SELECT COUNT(*) FROM silver_source").fetchone()[0] == 0


def test_reingest_same_bytes_overwrites_rather_than_duplicates() -> None:
    db = sqlite3.connect(":memory:")
    create_schema(db)
    silver = DefaultSilverProcessor(
        documents_media_writer=SqliteDocumentsMediaWriter(db),
        silver_source_writer=SqliteSilverSourceWriter(db),
    )
    raw = _bronze()
    _process(silver, raw, _extracted("old body"))
    _process(silver, raw, _extracted("new body"))
    db.commit()

    rows = db.execute("SELECT markdown FROM silver_source WHERE hash = ?", (raw.content_hash,)).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "new body"
