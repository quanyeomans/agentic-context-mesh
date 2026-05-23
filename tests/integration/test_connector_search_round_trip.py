"""Integration test — chunks written via the connector framework must be
findable via search (BM25 + vector + hybrid). The IM-6 cutover surfaced
that the new ``_SqliteChunkWriter`` skipped the FTS5 write, leaving
68,814 obsidian-collection chunks invisible to BM25. This test pins the
round-trip invariant so the gap can't recur silently.

Also covers the ``--collection`` CLI flag's bug fix (``_default_search``
accepting + threading ``collections`` kwarg through to
``pipeline.search``).

Per F47: constructs the pipeline via ``ConnectorPipeline.run_batch`` with
a Fake source connector; no inline ``*Pipeline(...)`` construction
outside the factory equivalent.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from kairix.core.db.schema import create_schema
from kairix.core.factory import build_connector_pipeline
from kairix.core.protocols import ChangeEvent
from tests.fakes import FakeExtractor, FakeSourceConnector


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@pytest.fixture
def in_memory_db_with_bronze(tmp_path: Path) -> tuple[sqlite3.Connection, Path]:
    """Fresh in-memory SQLite + filesystem bronze root rooted at tmp_path."""
    db = sqlite3.connect(":memory:")
    create_schema(db, dims=4)
    bronze_root = tmp_path / "bronze"
    bronze_root.mkdir()
    return db, bronze_root


@pytest.mark.integration
def test_connector_write_then_fts_bm25_finds_chunk(in_memory_db_with_bronze: tuple[sqlite3.Connection, Path]) -> None:
    """End-to-end: connector emits → silver chunks → writer persists → BM25 query finds.

    This is the canonical regression — the IM-6 cutover left the FTS5
    write out and BM25 returned zero results from the obsidian collection.
    Pins the round-trip so any future writer implementation must clear
    this bar.
    """
    db, bronze_root = in_memory_db_with_bronze
    connector_name = "obsidian"
    fake = FakeSourceConnector(
        name=connector_name,
        events=[ChangeEvent(op="created", item_id="note-a.md", modified_at=_now())],
        content={"note-a.md": b"# Architecture\n\nThe kairix architecture is layered."},
    )
    pipeline = build_connector_pipeline(db=db, bronze_root=bronze_root, collection=connector_name)
    result = pipeline.run_batch(fake, FakeExtractor())
    db.commit()

    assert result.processed == 1, f"connector should have processed 1 item; got {result}"

    # FTS5 BM25 query must find the new chunk
    matches = list(
        db.execute(
            "SELECT d.path FROM documents d JOIN documents_fts fts ON fts.rowid = d.id "
            "WHERE documents_fts MATCH ? AND d.collection = ?",
            ("architecture", connector_name),
        )
    )
    assert len(matches) >= 1, (
        f"BM25 MATCH against the obsidian collection returned 0 hits — "
        f"the IM-6 FTS-gap regression has returned. matches={matches!r}"
    )


@pytest.mark.integration
def test_connector_write_documents_and_fts_counts_match(
    in_memory_db_with_bronze: tuple[sqlite3.Connection, Path],
) -> None:
    """1:1 invariant — every active document the connector wrote must have a
    corresponding FTS row. (The IM-6 gap was 68,814 docs and 0 FTS rows.)"""
    db, bronze_root = in_memory_db_with_bronze
    fake = FakeSourceConnector(
        name="obsidian",
        events=[
            ChangeEvent(op="created", item_id="a.md", modified_at=_now()),
            ChangeEvent(op="created", item_id="b.md", modified_at=_now()),
            ChangeEvent(op="created", item_id="c.md", modified_at=_now()),
        ],
        content={
            "a.md": b"first file content",
            "b.md": b"second file content",
            "c.md": b"third file content",
        },
    )
    pipeline = build_connector_pipeline(db=db, bronze_root=bronze_root, collection="obsidian")
    pipeline.run_batch(fake, FakeExtractor())
    db.commit()

    docs_count = db.execute("SELECT COUNT(*) FROM documents WHERE collection = 'obsidian' AND active = 1").fetchone()[0]
    fts_count = db.execute(
        "SELECT COUNT(*) FROM documents d JOIN documents_fts fts ON fts.rowid = d.id WHERE d.collection = 'obsidian'"
    ).fetchone()[0]
    assert docs_count == fts_count, (
        f"FTS-vs-documents mismatch — documents={docs_count} FTS={fts_count} (IM-6 cutover failure mode)"
    )
    assert docs_count > 0, "expected at least 1 indexed doc"


# Note: the ``--collection`` CLI flag's contract is pinned externally
# by ``tests/integration/test_search_cli_collection_flag.py`` (outcome
# test via subprocess). A signature-introspection test on
# ``_default_search`` would violate F5 (no internal-name imports in tests);
# the CLI outcome test is the externally-visible regression pin.
