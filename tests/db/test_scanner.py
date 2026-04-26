"""Tests for kairix.db.scanner — document store file discovery, hashing, and ingestion."""

import sqlite3

import pytest

from kairix.db.scanner import CollectionConfig, ScanReport, VaultScanner, _hash_content
from kairix.reflib.frontmatter import extract_title as _extract_title


@pytest.mark.unit
def test_hash_content_deterministic() -> None:
    """Same content produces same hash."""
    assert _hash_content("hello world") == _hash_content("hello world")


@pytest.mark.unit
def test_hash_content_different_for_different_text() -> None:
    """Different content produces different hashes."""
    assert _hash_content("hello") != _hash_content("world")


@pytest.mark.unit
def test_extract_title_from_frontmatter() -> None:
    """Extracts title from YAML frontmatter."""
    text = "---\ntitle: My Document\ntype: note\n---\n\nBody text here."
    assert _extract_title(text, __import__("pathlib").Path("test.md")) == "My Document"


@pytest.mark.unit
def test_extract_title_from_heading() -> None:
    """Falls back to first # heading when no frontmatter title."""
    text = "# Hello World\n\nSome content."
    assert _extract_title(text, __import__("pathlib").Path("test.md")) == "Hello World"


@pytest.mark.unit
def test_extract_title_from_filename() -> None:
    """Falls back to filename when no frontmatter or heading."""
    text = "Just plain text with no heading."
    assert _extract_title(text, __import__("pathlib").Path("my-document.md")) == "My Document"


@pytest.mark.unit
def test_scan_discovers_new_files(tmp_path: __import__("pathlib").Path) -> None:
    """Scanner discovers new markdown files and inserts them."""
    vault = tmp_path / "vault"
    area = vault / "02-Areas"
    area.mkdir(parents=True)

    (area / "doc1.md").write_text("# Document One\n\nContent of doc one.")
    (area / "doc2.md").write_text("---\ntitle: Doc Two\n---\n\nContent of doc two.")

    db = sqlite3.connect(":memory:")
    db.executescript("""
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collection TEXT NOT NULL,
            path TEXT NOT NULL,
            title TEXT,
            hash TEXT NOT NULL,
            created_at TEXT,
            modified_at TEXT,
            active INTEGER DEFAULT 1,
            UNIQUE(collection, path)
        );
        CREATE TABLE content (hash TEXT PRIMARY KEY, doc TEXT, created_at TEXT);
    """)

    scanner = VaultScanner(db, vault)
    report = scanner.scan([CollectionConfig(name="vault-areas", path="02-Areas")])

    assert report.new == 2
    assert report.unchanged == 0
    assert report.removed == 0

    docs = db.execute("SELECT path, title FROM documents ORDER BY path").fetchall()
    assert len(docs) == 2
    assert docs[0][1] == "Document One"
    assert docs[1][1] == "Doc Two"


@pytest.mark.unit
def test_scan_detects_unchanged_files(tmp_path: __import__("pathlib").Path) -> None:
    """Unchanged files are not re-inserted."""
    vault = tmp_path / "vault"
    area = vault / "02-Areas"
    area.mkdir(parents=True)
    (area / "doc.md").write_text("# Stable\n\nUnchanged content.")

    db = sqlite3.connect(":memory:")
    db.executescript("""
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collection TEXT NOT NULL, path TEXT NOT NULL, title TEXT,
            hash TEXT NOT NULL, created_at TEXT, modified_at TEXT,
            active INTEGER DEFAULT 1, UNIQUE(collection, path)
        );
        CREATE TABLE content (hash TEXT PRIMARY KEY, doc TEXT, created_at TEXT);
    """)

    scanner = VaultScanner(db, vault)
    r1 = scanner.scan([CollectionConfig(name="test", path="02-Areas")])
    assert r1.new == 1

    r2 = scanner.scan([CollectionConfig(name="test", path="02-Areas")])
    assert r2.unchanged == 1
    assert r2.new == 0


@pytest.mark.unit
def test_scan_detects_updated_files(tmp_path: __import__("pathlib").Path) -> None:
    """Modified files are detected by hash change."""
    vault = tmp_path / "vault"
    area = vault / "02-Areas"
    area.mkdir(parents=True)
    doc = area / "doc.md"
    doc.write_text("# Version 1\n\nOriginal.")

    db = sqlite3.connect(":memory:")
    db.executescript("""
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collection TEXT NOT NULL, path TEXT NOT NULL, title TEXT,
            hash TEXT NOT NULL, created_at TEXT, modified_at TEXT,
            active INTEGER DEFAULT 1, UNIQUE(collection, path)
        );
        CREATE TABLE content (hash TEXT PRIMARY KEY, doc TEXT, created_at TEXT);
    """)

    scanner = VaultScanner(db, vault)
    scanner.scan([CollectionConfig(name="test", path="02-Areas")])

    doc.write_text("# Version 2\n\nUpdated.")
    r2 = scanner.scan([CollectionConfig(name="test", path="02-Areas")])
    assert r2.updated == 1
    assert r2.new == 0


@pytest.mark.unit
def test_scan_marks_removed_files_inactive(tmp_path: __import__("pathlib").Path) -> None:
    """Deleted files are marked as active=0."""
    vault = tmp_path / "vault"
    area = vault / "02-Areas"
    area.mkdir(parents=True)
    doc = area / "doc.md"
    doc.write_text("# Will Be Removed")

    db = sqlite3.connect(":memory:")
    db.executescript("""
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collection TEXT NOT NULL, path TEXT NOT NULL, title TEXT,
            hash TEXT NOT NULL, created_at TEXT, modified_at TEXT,
            active INTEGER DEFAULT 1, UNIQUE(collection, path)
        );
        CREATE TABLE content (hash TEXT PRIMARY KEY, doc TEXT, created_at TEXT);
    """)

    scanner = VaultScanner(db, vault)
    scanner.scan([CollectionConfig(name="test", path="02-Areas")])
    assert db.execute("SELECT active FROM documents").fetchone()[0] == 1

    doc.unlink()
    r2 = scanner.scan([CollectionConfig(name="test", path="02-Areas")])
    assert r2.removed == 1
    assert db.execute("SELECT active FROM documents").fetchone()[0] == 0


@pytest.mark.unit
def test_scan_excludes_patterns(tmp_path: __import__("pathlib").Path) -> None:
    """Exclude patterns filter out matching files."""
    vault = tmp_path / "vault"
    area = vault / "02-Areas"
    (area / "templates").mkdir(parents=True)
    (area / "real.md").write_text("# Real")
    (area / "templates" / "tmpl.md").write_text("# Template")

    db = sqlite3.connect(":memory:")
    db.executescript("""
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collection TEXT NOT NULL, path TEXT NOT NULL, title TEXT,
            hash TEXT NOT NULL, created_at TEXT, modified_at TEXT,
            active INTEGER DEFAULT 1, UNIQUE(collection, path)
        );
        CREATE TABLE content (hash TEXT PRIMARY KEY, doc TEXT, created_at TEXT);
    """)

    scanner = VaultScanner(db, vault)
    report = scanner.scan(
        [
            CollectionConfig(name="test", path="02-Areas", exclude=["templates"]),
        ]
    )
    assert report.new == 1


@pytest.mark.unit
def test_scan_report_str() -> None:
    """ScanReport has a useful string representation."""
    r = ScanReport(new=3, updated=1, removed=2, unchanged=10, collections_scanned=2)
    assert "3 new" in str(r)
    assert "1 updated" in str(r)
    assert "2 removed" in str(r)
