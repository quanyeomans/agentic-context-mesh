"""
Tests for mnemosyne.embed.schema.get_date_filtered_paths.

Verifies:
  - Empty frozenset when both bounds are None
  - Returns correct paths for rows within window
  - Excludes paths outside window
  - Both boundary dates are inclusive
  - Single-bound queries (start-only, end-only)
  - Returns empty frozenset on DB error (no raise)
  - NULL chunk_dates are excluded regardless of window
"""

from __future__ import annotations

import sqlite3
from datetime import date

from mnemosyne.embed.schema import get_date_filtered_paths

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db_with_dated_chunks() -> sqlite3.Connection:
    """
    Return an in-memory DB with content_vectors + documents tables.

    Documents and chunks:
      hash='h1' path='old.md'     chunk_date='2026-01-10'
      hash='h2' path='mid.md'     chunk_date='2026-03-15'
      hash='h3' path='recent.md'  chunk_date='2026-04-08'
      hash='h4' path='nodated.md' chunk_date=NULL
    """
    db = sqlite3.connect(":memory:")
    db.executescript("""
        CREATE TABLE documents (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            hash       TEXT NOT NULL,
            path       TEXT NOT NULL,
            collection TEXT NOT NULL DEFAULT 'vault',
            title      TEXT,
            active     INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE content_vectors (
            hash       TEXT NOT NULL,
            seq        INTEGER NOT NULL DEFAULT 0,
            pos        INTEGER NOT NULL DEFAULT 0,
            model      TEXT NOT NULL DEFAULT 'test',
            embedded_at TEXT NOT NULL DEFAULT '0',
            chunk_date TEXT,
            PRIMARY KEY (hash, seq)
        );

        INSERT INTO documents (hash, path, collection) VALUES
            ('h1', 'old.md',     'vault'),
            ('h2', 'mid.md',     'vault'),
            ('h3', 'recent.md',  'vault'),
            ('h4', 'nodated.md', 'vault');

        INSERT INTO content_vectors (hash, seq, chunk_date) VALUES
            ('h1', 0, '2026-01-10'),
            ('h2', 0, '2026-03-15'),
            ('h3', 0, '2026-04-08'),
            ('h4', 0, NULL);
    """)
    db.commit()
    return db


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_both_none_returns_empty() -> None:
    """When both start and end are None, returns empty frozenset immediately."""
    db = _make_db_with_dated_chunks()
    result = get_date_filtered_paths(db, start=None, end=None)
    assert result == frozenset()


def test_returns_paths_in_window() -> None:
    """Paths with chunk_date in [start, end] are returned."""
    db = _make_db_with_dated_chunks()
    result = get_date_filtered_paths(db, start=date(2026, 3, 1), end=date(2026, 4, 30))
    assert "mid.md" in result
    assert "recent.md" in result
    assert "old.md" not in result


def test_excludes_paths_outside_window() -> None:
    """Paths with chunk_date outside window are excluded."""
    db = _make_db_with_dated_chunks()
    result = get_date_filtered_paths(db, start=date(2026, 4, 1), end=date(2026, 4, 30))
    assert "recent.md" in result
    assert "old.md" not in result
    assert "mid.md" not in result


def test_null_chunk_dates_always_excluded() -> None:
    """Documents with chunk_date=NULL are never included regardless of window."""
    db = _make_db_with_dated_chunks()
    # Wide window that would include everything if NULLs weren't filtered
    result = get_date_filtered_paths(db, start=date(2000, 1, 1), end=date(2099, 12, 31))
    assert "nodated.md" not in result


def test_start_boundary_inclusive() -> None:
    """Start date boundary is inclusive."""
    db = _make_db_with_dated_chunks()
    result = get_date_filtered_paths(db, start=date(2026, 1, 10), end=date(2026, 2, 1))
    assert "old.md" in result  # chunk_date='2026-01-10' == start


def test_end_boundary_inclusive() -> None:
    """End date boundary is inclusive."""
    db = _make_db_with_dated_chunks()
    result = get_date_filtered_paths(db, start=date(2026, 3, 1), end=date(2026, 4, 8))
    assert "recent.md" in result  # chunk_date='2026-04-08' == end


def test_start_only_no_upper_bound() -> None:
    """When end=None, all paths from start onward are returned."""
    db = _make_db_with_dated_chunks()
    result = get_date_filtered_paths(db, start=date(2026, 3, 15), end=None)
    assert "mid.md" in result
    assert "recent.md" in result
    assert "old.md" not in result


def test_end_only_no_lower_bound() -> None:
    """When start=None, all paths up to end are returned."""
    db = _make_db_with_dated_chunks()
    result = get_date_filtered_paths(db, start=None, end=date(2026, 3, 14))
    assert "old.md" in result
    assert "mid.md" not in result
    assert "recent.md" not in result


def test_empty_window_returns_empty() -> None:
    """Window that contains no dated chunks returns empty frozenset."""
    db = _make_db_with_dated_chunks()
    result = get_date_filtered_paths(db, start=date(2025, 1, 1), end=date(2025, 12, 31))
    assert result == frozenset()


def test_returns_frozenset() -> None:
    """Return type is always frozenset."""
    db = _make_db_with_dated_chunks()
    result = get_date_filtered_paths(db, start=date(2026, 1, 1), end=date(2026, 12, 31))
    assert isinstance(result, frozenset)


def test_db_error_returns_empty_not_raises() -> None:
    """On DB error, returns empty frozenset without raising."""
    db = sqlite3.connect(":memory:")  # no tables — will error on query
    result = get_date_filtered_paths(db, start=date(2026, 1, 1), end=date(2026, 4, 30))
    assert result == frozenset()


def test_multiple_chunks_same_document_deduplicated() -> None:
    """Multiple chunks (seq>0) for same document path appear only once in result."""
    db = sqlite3.connect(":memory:")
    db.executescript("""
        CREATE TABLE documents (
            hash TEXT NOT NULL, path TEXT NOT NULL,
            collection TEXT NOT NULL DEFAULT 'vault', active INTEGER DEFAULT 1
        );
        CREATE TABLE content_vectors (
            hash TEXT NOT NULL, seq INTEGER NOT NULL DEFAULT 0,
            model TEXT NOT NULL DEFAULT 'test', embedded_at TEXT NOT NULL DEFAULT '0',
            chunk_date TEXT, pos INTEGER DEFAULT 0
        );
        INSERT INTO documents VALUES ('h1', 'multi.md', 'vault', 1);
        INSERT INTO content_vectors VALUES ('h1', 0, 'test', '0', '2026-04-08', 0);
        INSERT INTO content_vectors VALUES ('h1', 1, 'test', '0', '2026-04-08', 1);
        INSERT INTO content_vectors VALUES ('h1', 2, 'test', '0', '2026-04-08', 2);
    """)
    db.commit()
    result = get_date_filtered_paths(db, start=date(2026, 4, 1), end=date(2026, 4, 30))
    assert result == frozenset({"multi.md"})
