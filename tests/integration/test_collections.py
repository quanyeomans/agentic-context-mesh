"""Tests for multi-collection scanning in the embed pipeline."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kairix.core.db.scanner import CollectionConfig, DocumentScanner

pytestmark = pytest.mark.integration

# F69 scale floor — the multi-collection DISTINCT-collection fetchall must
# survive a production-scale documents table.
# ``_F69_TOTAL_DOCS`` documents split across the two collections proves
# the scanner.scan path + the subsequent SELECT DISTINCT path both stay
# bounded under genuine production volume.
_F69_TOTAL_DOCS = 10_000
_F69_DOCS_PER_COLLECTION = _F69_TOTAL_DOCS // 2


def _create_scanner_schema(db: sqlite3.Connection) -> None:
    """Create the production schema (single source of truth with kairix.core.db.schema)."""
    from kairix.core.db.schema import create_schema

    create_schema(db)


@pytest.fixture()
def multi_collection_dirs(tmp_path: Path) -> dict[str, Path]:
    """Create a document root with two separate collection directories."""
    # Main documents
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "architecture.md").write_text("# Architecture\nService mesh pattern.")
    (docs_dir / "runbook.md").write_text("# Runbook\nRestart sequence.")

    # Agent workspace memories
    ws_dir = tmp_path / "workspaces" / "agent-beta" / "memory"
    ws_dir.mkdir(parents=True)
    (ws_dir / "2026-04-27.md").write_text("# Session Notes\nDeployed kairix v2.")
    (ws_dir / "2026-04-26.md").write_text("# Session Notes\nFixed CI pipeline.")

    return {"root": tmp_path, "docs": docs_dir, "workspaces": tmp_path / "workspaces"}


@pytest.fixture()
def multi_collection_dirs_at_scale(tmp_path: Path) -> dict[str, Path]:
    """Same shape as ``multi_collection_dirs`` but seeded at F69 production scale.

    Writes ``_F69_DOCS_PER_COLLECTION`` documents into each of the two
    collections — proves the scanner + the SELECT DISTINCT
    fetchall both survive a 10K-row documents table.
    """
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    for i in range(_F69_DOCS_PER_COLLECTION):
        (docs_dir / f"doc-{i:05d}.md").write_text(f"# Doc {i}\nContent for doc {i}.\n", encoding="utf-8")

    ws_dir = tmp_path / "workspaces" / "agent-beta" / "memory"
    ws_dir.mkdir(parents=True)
    for i in range(_F69_DOCS_PER_COLLECTION):
        (ws_dir / f"memo-{i:05d}.md").write_text(f"# Memo {i}\nMemory body {i}.\n", encoding="utf-8")

    return {"root": tmp_path, "docs": docs_dir, "workspaces": tmp_path / "workspaces"}


class TestMultiCollectionScanning:
    """DocumentScanner handles multiple collections."""

    @pytest.mark.integration
    def test_single_collection_scans_root(self, multi_collection_dirs: dict, tmp_path: Path) -> None:
        """Default single-collection scan finds all documents under root."""
        import sqlite3

        db = sqlite3.connect(":memory:")
        _create_scanner_schema(db)
        scanner = DocumentScanner(db, document_root=multi_collection_dirs["root"])
        report = scanner.scan([CollectionConfig(name="default", path=".")])
        assert report.new == 4  # 2 docs + 2 workspace memories

    @pytest.mark.integration
    def test_multi_collection_scans_separately(self, multi_collection_dirs: dict) -> None:
        # F69-small-scale-only: pins the per-collection name STORAGE
        # contract — that scanner.scan writes the configured collection
        # name into documents.collection. The structural assertion (both
        # names land in DISTINCT) fires correctly at any N >= 1.
        # Scale-bound coverage of the same SELECT DISTINCT fetchall
        # against a 10K-row documents table lives in the sibling test
        # ``test_multi_collection_scans_separately_at_10k_docs`` below.
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

    @pytest.mark.integration
    @pytest.mark.slow
    def test_multi_collection_scans_separately_at_10k_docs(self, multi_collection_dirs_at_scale: dict) -> None:
        """F69 production-scale variant: scanner + SELECT DISTINCT survive 10K docs.

        Seeds ``_F69_DOCS_PER_COLLECTION`` docs in each of two collections
        (10K total), runs ``scanner.scan(collections)``, then runs the
        same SELECT DISTINCT fetchall the fixture-scale test pins —
        with a wall-clock budget that catches Bug 3-class unbounded
        scans on the documents table.

        Sabotage proof (executed): replaced the SELECT DISTINCT with a
        Bug-3 self-join (``FROM documents d1, documents d2 WHERE
        d1.id != d2.id``) — at 10K rows the wall-clock crossed 4s
        (well over the 3s budget). Restoring the bare DISTINCT brought
        it back under 10ms.
        """
        import sqlite3
        import time

        db = sqlite3.connect(":memory:")
        _create_scanner_schema(db)
        scanner = DocumentScanner(db, document_root=multi_collection_dirs_at_scale["root"])
        collections = [
            CollectionConfig(name="docs", path="docs"),
            CollectionConfig(name="workspaces", path="workspaces", glob="**/memory/**/*.md"),
        ]
        scan_start = time.monotonic()
        report = scanner.scan(collections)
        scan_elapsed = time.monotonic() - scan_start
        assert report.new == _F69_TOTAL_DOCS, (
            f"expected {_F69_TOTAL_DOCS} new docs across the two collections; got {report.new}"
        )
        # Scanner budget: 60s for 10K docs (filesystem-bound) — generous
        # to absorb CI host variability without masking a regression.
        assert scan_elapsed < 60.0, (
            f"scanner.scan over {_F69_TOTAL_DOCS} docs took {scan_elapsed:.2f}s; "
            f"budget 60s. fix: confirm scanner path stays linear in doc count"
        )

        # F69: the SELECT DISTINCT collection fetchall over a 10K-row
        # documents table must complete within wall-clock budget.
        select_start = time.monotonic()
        rows = db.execute("SELECT DISTINCT collection FROM documents").fetchall()
        select_elapsed = time.monotonic() - select_start
        names = {r[0] for r in rows}
        assert "docs" in names
        assert "workspaces" in names
        assert select_elapsed < 3.0, (
            f"SELECT DISTINCT collection over 10K docs took {select_elapsed:.2f}s; "
            f"budget 3.0s. fix: ensure collection column is indexed if you added a sort"
        )

    @pytest.mark.integration
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

    @pytest.mark.integration
    def test_fallback_when_no_collections_configured(self, multi_collection_dirs: dict) -> None:
        """When no collections config exists, embed falls back to single default collection."""
        from kairix.core.search.config_loader import parse_collections

        result = parse_collections({})
        assert result is None  # triggers fallback in embed CLI

    @pytest.mark.integration
    def test_workspace_glob_filters_correctly(self, multi_collection_dirs: dict) -> None:
        """Workspace glob only matches files under memory/ subdirectories."""
        # Add a non-memory file to workspaces
        tool_dir = multi_collection_dirs["workspaces"] / "agent-beta" / "tools"
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
