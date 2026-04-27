"""Tests for multi-collection scanning in the embed pipeline."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kairix.db.scanner import CollectionConfig, DocumentScanner

pytestmark = pytest.mark.integration


def _create_scanner_schema(db: sqlite3.Connection) -> None:
    """Create minimal schema for DocumentScanner (no sqlite-vec needed)."""
    db.executescript("""
        CREATE TABLE IF NOT EXISTS documents (
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
        CREATE TABLE IF NOT EXISTS content (
            hash TEXT PRIMARY KEY,
            doc TEXT,
            created_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_documents_hash ON documents(hash);
        CREATE INDEX IF NOT EXISTS idx_documents_collection ON documents(collection);
        CREATE INDEX IF NOT EXISTS idx_documents_active ON documents(active);
    """)


@pytest.fixture()
def multi_collection_dirs(tmp_path: Path) -> dict[str, Path]:
    """Create a document root with two separate collection directories."""
    # Main documents
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "architecture.md").write_text("# Architecture\nService mesh pattern.")
    (docs_dir / "runbook.md").write_text("# Runbook\nRestart sequence.")

    # Agent workspace memories
    ws_dir = tmp_path / "workspaces" / "builder" / "memory"
    ws_dir.mkdir(parents=True)
    (ws_dir / "2026-04-27.md").write_text("# Session Notes\nDeployed kairix v2.")
    (ws_dir / "2026-04-26.md").write_text("# Session Notes\nFixed CI pipeline.")

    return {"root": tmp_path, "docs": docs_dir, "workspaces": tmp_path / "workspaces"}


class TestMultiCollectionScanning:
    """DocumentScanner handles multiple collections."""

    def test_single_collection_scans_root(self, multi_collection_dirs: dict, tmp_path: Path) -> None:
        """Default single-collection scan finds all documents under root."""
        import sqlite3

        db = sqlite3.connect(":memory:")
        _create_scanner_schema(db)
        scanner = DocumentScanner(db, document_root=multi_collection_dirs["root"])
        report = scanner.scan([CollectionConfig(name="default", path=".")])
        assert report.new == 4  # 2 docs + 2 workspace memories

    def test_multi_collection_scans_separately(self, multi_collection_dirs: dict) -> None:
        """Multiple collections scan their own directories."""
        import sqlite3

        db = sqlite3.connect(":memory:")
        _create_scanner_schema(db)
        scanner = DocumentScanner(db, document_root=multi_collection_dirs["root"])
        collections = [
            CollectionConfig(name="docs", path="docs"),
            CollectionConfig(name="workspaces", path="workspaces", glob="**/memory/**/*.md"),
        ]
        report = scanner.scan(collections)
        assert report.new == 4  # 2 + 2

        # Verify collection names are stored
        rows = db.execute("SELECT DISTINCT collection FROM documents").fetchall()
        names = {r[0] for r in rows}
        assert "docs" in names
        assert "workspaces" in names

    def test_empty_collection_returns_zero(self, tmp_path: Path) -> None:
        """A collection pointing to an empty directory returns 0 new."""
        import sqlite3

        db = sqlite3.connect(":memory:")
        _create_scanner_schema(db)
        empty = tmp_path / "empty"
        empty.mkdir()
        scanner = DocumentScanner(db, document_root=tmp_path)
        report = scanner.scan([CollectionConfig(name="empty", path="empty")])
        assert report.new == 0

    def test_fallback_when_no_collections_configured(self, multi_collection_dirs: dict) -> None:
        """When no collections config exists, embed falls back to single default collection."""
        from kairix.search.config_loader import parse_collections

        result = parse_collections({})
        assert result is None  # triggers fallback in embed CLI

    def test_workspace_glob_filters_correctly(self, multi_collection_dirs: dict) -> None:
        """Workspace glob only matches files under memory/ subdirectories."""
        # Add a non-memory file to workspaces
        tool_dir = multi_collection_dirs["workspaces"] / "builder" / "tools"
        tool_dir.mkdir(parents=True)
        (tool_dir / "output.md").write_text("# Tool Output\nThis should be excluded.")

        db = sqlite3.connect(":memory:")
        _create_scanner_schema(db)
        scanner = DocumentScanner(db, document_root=multi_collection_dirs["root"])
        report = scanner.scan(
            [
                CollectionConfig(name="workspaces", path="workspaces", glob="**/memory/**/*.md"),
            ]
        )
        # Only 2 memory files, not the tool output
        assert report.new == 2
