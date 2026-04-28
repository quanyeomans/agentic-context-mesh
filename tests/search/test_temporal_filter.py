"""
Tests for TMP-2: date-range path filtering in BM25 search.

Covers:
  - bm25_search date_filter_paths post-filter
  - Graceful degradation when date_filter_paths is empty or None
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from kairix.search.bm25 import bm25_search


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_test_db(tmp_path: Path) -> Path:
    """Create a test DB with two documents for date filter testing."""
    db_path = tmp_path / "test.sqlite"
    db = sqlite3.connect(str(db_path))
    db.executescript("""
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collection TEXT NOT NULL, path TEXT NOT NULL, title TEXT,
            hash TEXT NOT NULL, active INTEGER DEFAULT 1,
            UNIQUE(collection, path)
        );
        CREATE TABLE content (hash TEXT PRIMARY KEY, doc TEXT, created_at TEXT);
        CREATE VIRTUAL TABLE documents_fts USING fts5(
            title, doc, content='', tokenize='porter unicode61'
        );

        INSERT INTO documents (collection, path, title, hash, active)
        VALUES ('vault-areas', '02-Areas/good.md', 'Good Doc', 'h1', 1);
        INSERT INTO content (hash, doc) VALUES ('h1', 'This is a test document for filtering.');

        INSERT INTO documents (collection, path, title, hash, active)
        VALUES ('vault-areas', '02-Areas/bad.md', 'Bad Doc', 'h2', 1);
        INSERT INTO content (hash, doc) VALUES ('h2', 'Another test document for filtering.');

        INSERT INTO documents_fts(rowid, title, doc) SELECT d.id, d.title, c.doc
        FROM documents d JOIN content c ON c.hash = d.hash WHERE d.active = 1;
    """)
    db.close()
    return db_path


# ---------------------------------------------------------------------------
# bm25_search date_filter_paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_bm25_date_filter_none_no_filtering(tmp_path: Path) -> None:
    """date_filter_paths=None -> results not filtered."""
    db_path = _create_test_db(tmp_path)
    with patch("kairix.search.bm25.get_db_path", return_value=db_path):
        results = bm25_search("test document filtering", date_filter_paths=None)

    assert len(results) == 2


@pytest.mark.unit
def test_bm25_date_filter_empty_no_filtering(tmp_path: Path) -> None:
    """date_filter_paths=frozenset() -> results not filtered (empty = no-filter)."""
    db_path = _create_test_db(tmp_path)
    with patch("kairix.search.bm25.get_db_path", return_value=db_path):
        results = bm25_search("test document filtering", date_filter_paths=frozenset())

    assert len(results) == 2


@pytest.mark.unit
def test_bm25_date_filter_applied(tmp_path: Path) -> None:
    """date_filter_paths with one path -> only matching result returned."""
    db_path = _create_test_db(tmp_path)
    with patch("kairix.search.bm25.get_db_path", return_value=db_path):
        results = bm25_search(
            "test document filtering",
            date_filter_paths=frozenset({"02-Areas/good.md"}),
        )

    assert len(results) == 1
    assert results[0]["file"] == "02-Areas/good.md"
