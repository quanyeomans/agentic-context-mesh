"""Unit tests for kairix.core.search.pages — paged per-page text read path (PLA-270).

Each test builds a real worker schema and writes ``document_pages`` rows via
the production :class:`~kairix.core.connectors.silver.SqliteDocumentPagesWriter`
plus the ``silver_source`` bridge row, then reads back through the public
``page_text`` / ``lookup_page_text`` surface. No private helpers or
monkeypatches — the read path is exercised against the real schema.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kairix.core.connectors.silver import SqliteDocumentPagesWriter
from kairix.core.db.schema import create_schema
from kairix.core.protocols import Page
from kairix.core.search.pages import lookup_page_text, page_text

pytestmark = pytest.mark.unit

_RAW_HASH = "raw-bytes-hash-001"
_SOURCE_URI = "m365://drive/report.pdf"


def _seed_paged_doc(db: sqlite3.Connection) -> None:
    """Write the document_pages rows + the silver_source bridge for one paged doc."""
    create_schema(db)
    SqliteDocumentPagesWriter(db).write_pages(
        content_hash=_RAW_HASH,
        pages=[
            Page(page_number=1, text="page one body", has_images=False),
            Page(page_number=2, text="page two body — the answer is here", has_images=True),
            Page(page_number=3, text="page three body", has_images=False),
        ],
    )
    db.execute(
        "INSERT INTO silver_source (hash, source_uri, markdown, created_at) VALUES (?, ?, ?, ?)",
        (_RAW_HASH, _SOURCE_URI, "full markdown", "2026-06-30T00:00:00Z"),
    )
    db.commit()


def test_page_text_returns_stored_text_for_a_paged_hit() -> None:
    db = sqlite3.connect(":memory:")
    _seed_paged_doc(db)
    assert page_text(source_uri=_SOURCE_URI, page_number=2, db=db) == "page two body — the answer is here"


def test_page_text_resolves_each_page_independently() -> None:
    db = sqlite3.connect(":memory:")
    _seed_paged_doc(db)
    assert page_text(source_uri=_SOURCE_URI, page_number=1, db=db) == "page one body"
    assert page_text(source_uri=_SOURCE_URI, page_number=3, db=db) == "page three body"


def test_page_text_out_of_range_page_returns_none() -> None:
    db = sqlite3.connect(":memory:")
    _seed_paged_doc(db)
    assert page_text(source_uri=_SOURCE_URI, page_number=99, db=db) is None


def test_page_text_unknown_source_uri_returns_none() -> None:
    db = sqlite3.connect(":memory:")
    _seed_paged_doc(db)
    assert page_text(source_uri="m365://drive/other.pdf", page_number=1, db=db) is None


def test_page_text_empty_source_uri_returns_none() -> None:
    db = sqlite3.connect(":memory:")
    _seed_paged_doc(db)
    # Empty breadcrumb short-circuits before touching the DB.
    assert page_text(source_uri="", page_number=1, db=db) is None


def test_page_text_without_silver_source_bridge_returns_none() -> None:
    """A page row with no silver_source bridge (legacy ingest) is unreachable → None."""
    db = sqlite3.connect(":memory:")
    create_schema(db)
    SqliteDocumentPagesWriter(db).write_pages(
        content_hash=_RAW_HASH,
        pages=[Page(page_number=1, text="orphan page", has_images=False)],
    )
    db.commit()
    assert page_text(source_uri=_SOURCE_URI, page_number=1, db=db) is None


def test_page_text_null_extracted_text_returns_none() -> None:
    """A stored page whose extracted_text is NULL resolves to None, not 'None'."""
    db = sqlite3.connect(":memory:")
    create_schema(db)
    db.execute(
        "INSERT INTO document_pages (hash, page_number, extracted_text, has_images, image_descriptions) "
        "VALUES (?, ?, NULL, 0, NULL)",
        (_RAW_HASH, 5),
    )
    db.execute(
        "INSERT INTO silver_source (hash, source_uri, markdown, created_at) VALUES (?, ?, ?, ?)",
        (_RAW_HASH, _SOURCE_URI, "md", "2026-06-30T00:00:00Z"),
    )
    db.commit()
    assert page_text(source_uri=_SOURCE_URI, page_number=5, db=db) is None


def test_page_text_swallows_lookup_errors_and_returns_none() -> None:
    """A DB with no schema (no such table) degrades to None rather than raising."""
    db = sqlite3.connect(":memory:")  # no create_schema → silver_source/document_pages absent
    assert page_text(source_uri=_SOURCE_URI, page_number=1, db=db) is None


def test_lookup_page_text_opens_db_and_resolves(tmp_path: Path) -> None:
    """The production convenience opens the worker DB at a path and resolves the page."""
    db_path = tmp_path / "worker.sqlite"
    db = sqlite3.connect(str(db_path))
    _seed_paged_doc(db)
    db.close()
    assert (
        lookup_page_text(source_uri=_SOURCE_URI, page_number=2, db_path=db_path) == "page two body — the answer is here"
    )


def test_lookup_page_text_missing_page_returns_none(tmp_path: Path) -> None:
    db_path = tmp_path / "worker.sqlite"
    db = sqlite3.connect(str(db_path))
    _seed_paged_doc(db)
    db.close()
    assert lookup_page_text(source_uri=_SOURCE_URI, page_number=42, db_path=db_path) is None


def test_lookup_page_text_unopenable_db_returns_none(tmp_path: Path) -> None:
    """An unopenable DB path (here: a directory) degrades to None, not a raise."""
    # tmp_path is a directory — sqlite3 cannot open it as a database file.
    assert lookup_page_text(source_uri=_SOURCE_URI, page_number=1, db_path=tmp_path) is None
