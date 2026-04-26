"""Unit tests for kairix.db.migrate_qmd — QMD-to-kairix migration."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from kairix.db.migrate_qmd import MigrationReport, migrate_from_qmd


def _create_qmd_db(path: Path) -> None:
    """Create a minimal QMD-style database with schema and sample data."""
    db = sqlite3.connect(str(path))
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

        CREATE TABLE content (
            hash TEXT PRIMARY KEY,
            doc TEXT,
            created_at TEXT
        );

        CREATE TABLE content_vectors (
            hash TEXT NOT NULL,
            seq INTEGER NOT NULL,
            pos INTEGER DEFAULT 0,
            model TEXT,
            embedded_at TEXT,
            PRIMARY KEY (hash, seq)
        );
    """)

    db.execute(
        "INSERT INTO documents (collection, path, title, hash, created_at, modified_at, active) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("engineering", "eng/adr-001.md", "ADR 001", "abc123", "2025-01-01", "2025-01-02", 1),
    )
    db.execute(
        "INSERT INTO documents (collection, path, title, hash, created_at, modified_at, active) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("philosophy", "phil/stoics.md", "Stoics", "def456", "2025-02-01", "2025-02-02", 1),
    )
    db.execute(
        "INSERT INTO content (hash, doc, created_at) VALUES (?, ?, ?)",
        ("abc123", "# ADR 001\nSome content", "2025-01-01"),
    )
    db.execute(
        "INSERT INTO content (hash, doc, created_at) VALUES (?, ?, ?)",
        ("def456", "# Stoics\nPhilosophy content", "2025-02-01"),
    )
    db.execute(
        "INSERT INTO content_vectors (hash, seq, pos, model, embedded_at) VALUES (?, ?, ?, ?, ?)",
        ("abc123", 0, 0, "text-embedding-3-large", "2025-01-01"),
    )
    db.commit()
    db.close()


def _create_empty_qmd_db(path: Path) -> None:
    """Create a QMD-style database with schema but no data."""
    db = sqlite3.connect(str(path))
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

        CREATE TABLE content (
            hash TEXT PRIMARY KEY,
            doc TEXT,
            created_at TEXT
        );

        CREATE TABLE content_vectors (
            hash TEXT NOT NULL,
            seq INTEGER NOT NULL,
            pos INTEGER DEFAULT 0,
            model TEXT,
            embedded_at TEXT,
            PRIMARY KEY (hash, seq)
        );
    """)
    db.commit()
    db.close()


class TestMigrationReport:
    @pytest.mark.unit
    def test_str_format(self):
        report = MigrationReport(documents=5, content_rows=3, content_vectors=2, vectors=10, fts_indexed=5)
        s = str(report)
        assert "5 documents" in s
        assert "3 content rows" in s
        assert "10 vectors" in s

    @pytest.mark.unit
    def test_defaults(self):
        report = MigrationReport()
        assert report.documents == 0
        assert report.fts_indexed == 0


class TestMigrateFromQMD:
    @pytest.mark.unit
    def test_missing_qmd_db_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="QMD database not found"):
            migrate_from_qmd(
                kairix_db_path=tmp_path / "kairix.db",
                qmd_db_path=tmp_path / "nonexistent.db",
            )

    @pytest.mark.unit
    @patch("kairix.db.migrate_qmd.load_extensions")
    @patch("kairix.db.migrate_qmd.create_schema")
    @patch("kairix.db.migrate_qmd.rebuild_fts")
    def test_empty_qmd_migration(self, mock_fts, mock_schema, mock_ext, tmp_path):
        mock_fts.return_value = 0

        def fake_schema(db, **kwargs):
            """Create tables without vec0 (not available in test)."""
            db.executescript("""
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    collection TEXT NOT NULL, path TEXT NOT NULL,
                    title TEXT, hash TEXT NOT NULL,
                    created_at TEXT, modified_at TEXT, active INTEGER DEFAULT 1,
                    UNIQUE(collection, path)
                );
                CREATE TABLE IF NOT EXISTS content (
                    hash TEXT PRIMARY KEY, doc TEXT, created_at TEXT
                );
                CREATE TABLE IF NOT EXISTS content_vectors (
                    hash TEXT NOT NULL, seq INTEGER NOT NULL,
                    pos INTEGER DEFAULT 0, model TEXT, embedded_at TEXT,
                    PRIMARY KEY (hash, seq)
                );
            """)

        mock_schema.side_effect = fake_schema

        qmd_path = tmp_path / "qmd.db"
        kairix_path = tmp_path / "kairix.db"
        _create_empty_qmd_db(qmd_path)

        report = migrate_from_qmd(kairix_path, qmd_path)
        assert report.documents == 0
        assert report.content_rows == 0
        assert report.content_vectors == 0
        assert kairix_path.exists()

    @pytest.mark.unit
    @patch("kairix.db.migrate_qmd.load_extensions")
    @patch("kairix.db.migrate_qmd.create_schema")
    @patch("kairix.db.migrate_qmd.rebuild_fts")
    def test_migration_with_data(self, mock_fts, mock_schema, mock_ext, tmp_path):
        mock_fts.return_value = 2

        def fake_schema(db, **kwargs):
            db.executescript("""
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    collection TEXT NOT NULL, path TEXT NOT NULL,
                    title TEXT, hash TEXT NOT NULL,
                    created_at TEXT, modified_at TEXT, active INTEGER DEFAULT 1,
                    UNIQUE(collection, path)
                );
                CREATE TABLE IF NOT EXISTS content (
                    hash TEXT PRIMARY KEY, doc TEXT, created_at TEXT
                );
                CREATE TABLE IF NOT EXISTS content_vectors (
                    hash TEXT NOT NULL, seq INTEGER NOT NULL,
                    pos INTEGER DEFAULT 0, model TEXT, embedded_at TEXT,
                    PRIMARY KEY (hash, seq)
                );
            """)

        mock_schema.side_effect = fake_schema

        qmd_path = tmp_path / "qmd.db"
        kairix_path = tmp_path / "kairix.db"
        _create_qmd_db(qmd_path)

        report = migrate_from_qmd(kairix_path, qmd_path)
        assert report.documents == 2
        assert report.content_rows == 2
        assert report.content_vectors == 1
        assert report.fts_indexed == 2

    @pytest.mark.unit
    @patch("kairix.db.migrate_qmd.load_extensions")
    @patch("kairix.db.migrate_qmd.create_schema")
    @patch("kairix.db.migrate_qmd.rebuild_fts")
    def test_idempotent_rerun(self, mock_fts, mock_schema, mock_ext, tmp_path):
        mock_fts.return_value = 2

        def fake_schema(db, **kwargs):
            db.executescript("""
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    collection TEXT NOT NULL, path TEXT NOT NULL,
                    title TEXT, hash TEXT NOT NULL,
                    created_at TEXT, modified_at TEXT, active INTEGER DEFAULT 1,
                    UNIQUE(collection, path)
                );
                CREATE TABLE IF NOT EXISTS content (
                    hash TEXT PRIMARY KEY, doc TEXT, created_at TEXT
                );
                CREATE TABLE IF NOT EXISTS content_vectors (
                    hash TEXT NOT NULL, seq INTEGER NOT NULL,
                    pos INTEGER DEFAULT 0, model TEXT, embedded_at TEXT,
                    PRIMARY KEY (hash, seq)
                );
            """)

        mock_schema.side_effect = fake_schema

        qmd_path = tmp_path / "qmd.db"
        kairix_path = tmp_path / "kairix.db"
        _create_qmd_db(qmd_path)

        report1 = migrate_from_qmd(kairix_path, qmd_path)
        assert report1.documents == 2

        # Second run — INSERT OR IGNORE means 0 new rows
        report2 = migrate_from_qmd(kairix_path, qmd_path)
        assert report2.documents == 0
        assert report2.content_rows == 0

    @pytest.mark.unit
    @patch("kairix.db.migrate_qmd.load_extensions")
    @patch("kairix.db.migrate_qmd.create_schema")
    @patch("kairix.db.migrate_qmd.rebuild_fts")
    def test_creates_target_directory(self, mock_fts, mock_schema, mock_ext, tmp_path):
        mock_fts.return_value = 0

        def fake_schema(db, **kwargs):
            db.executescript("""
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    collection TEXT NOT NULL, path TEXT NOT NULL,
                    title TEXT, hash TEXT NOT NULL,
                    created_at TEXT, modified_at TEXT, active INTEGER DEFAULT 1,
                    UNIQUE(collection, path)
                );
                CREATE TABLE IF NOT EXISTS content (
                    hash TEXT PRIMARY KEY, doc TEXT, created_at TEXT
                );
                CREATE TABLE IF NOT EXISTS content_vectors (
                    hash TEXT NOT NULL, seq INTEGER NOT NULL,
                    pos INTEGER DEFAULT 0, model TEXT, embedded_at TEXT,
                    PRIMARY KEY (hash, seq)
                );
            """)

        mock_schema.side_effect = fake_schema

        qmd_path = tmp_path / "qmd.db"
        _create_empty_qmd_db(qmd_path)

        deep_path = tmp_path / "a" / "b" / "kairix.db"
        migrate_from_qmd(deep_path, qmd_path)
        assert deep_path.exists()

    @pytest.mark.unit
    @patch("kairix.db.migrate_qmd.load_extensions")
    @patch("kairix.db.migrate_qmd.create_schema")
    @patch("kairix.db.migrate_qmd.rebuild_fts")
    def test_qmd_db_not_modified(self, mock_fts, mock_schema, mock_ext, tmp_path):
        mock_fts.return_value = 2

        def fake_schema(db, **kwargs):
            db.executescript("""
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    collection TEXT NOT NULL, path TEXT NOT NULL,
                    title TEXT, hash TEXT NOT NULL,
                    created_at TEXT, modified_at TEXT, active INTEGER DEFAULT 1,
                    UNIQUE(collection, path)
                );
                CREATE TABLE IF NOT EXISTS content (
                    hash TEXT PRIMARY KEY, doc TEXT, created_at TEXT
                );
                CREATE TABLE IF NOT EXISTS content_vectors (
                    hash TEXT NOT NULL, seq INTEGER NOT NULL,
                    pos INTEGER DEFAULT 0, model TEXT, embedded_at TEXT,
                    PRIMARY KEY (hash, seq)
                );
            """)

        mock_schema.side_effect = fake_schema

        qmd_path = tmp_path / "qmd.db"
        kairix_path = tmp_path / "kairix.db"
        _create_qmd_db(qmd_path)

        # Read QMD content before migration
        db = sqlite3.connect(str(qmd_path))
        before_count = db.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        db.close()

        migrate_from_qmd(kairix_path, qmd_path)

        # Verify QMD unchanged
        db = sqlite3.connect(str(qmd_path))
        after_count = db.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        db.close()

        assert before_count == after_count
