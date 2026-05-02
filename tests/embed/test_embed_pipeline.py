"""Unit tests for EmbedPipeline orchestrator."""

from __future__ import annotations

import sqlite3

import pytest

from kairix.core.embed.deps import EmbedDependencies
from kairix.core.embed.pipeline import EmbedPipeline


def _make_test_db() -> sqlite3.Connection:
    """Create an in-memory SQLite DB with the required schema for embedding."""
    db = sqlite3.connect(":memory:")
    db.execute("""
        CREATE TABLE documents (
            hash TEXT PRIMARY KEY,
            path TEXT NOT NULL,
            title TEXT,
            collection TEXT,
            active INTEGER DEFAULT 1
        )
        """)
    db.execute("""
        CREATE TABLE content (
            hash TEXT PRIMARY KEY,
            doc TEXT
        )
        """)
    db.execute("""
        CREATE TABLE content_vectors (
            hash TEXT,
            seq INTEGER,
            pos INTEGER,
            model TEXT,
            embedded_at INTEGER,
            chunk_date TEXT,
            PRIMARY KEY (hash, seq)
        )
        """)
    return db


def _make_fake_deps(embed_dim: int = 1536) -> EmbedDependencies:
    """Build EmbedDependencies with all-fake implementations."""
    return EmbedDependencies(
        get_azure_config=lambda: ("fake-key", "https://fake.endpoint", "fake-model"),
        preflight_check=lambda _k, _e, _d: embed_dim,
        embed_batch=lambda texts, *a, **kw: [[0.01] * embed_dim for _ in texts],
        open_usearch_index=lambda: None,
        migrate_content_vectors=lambda db: None,
        get_document_root=lambda: None,
    )


@pytest.mark.unit
class TestEmbedPipeline:
    @pytest.mark.unit
    def test_run_with_no_pending_chunks(self) -> None:
        """Pipeline returns zeros when no documents need embedding."""
        db = _make_test_db()
        pipeline = EmbedPipeline(db=db, deps=_make_fake_deps())

        result = pipeline.run()

        assert result["embedded"] == 0
        assert result["failed"] == 0

    @pytest.mark.unit
    def test_run_embeds_pending_document(self) -> None:
        """Pipeline embeds a single pending document chunk."""
        db = _make_test_db()
        db.execute(
            "INSERT INTO documents (hash, path, title, collection) VALUES (?, ?, ?, ?)",
            ("h1", "/docs/test.md", "Test", "default"),
        )
        db.execute(
            "INSERT INTO content (hash, doc) VALUES (?, ?)",
            ("h1", "This is a test document about embedding pipelines."),
        )
        db.commit()

        pipeline = EmbedPipeline(db=db, deps=_make_fake_deps())
        result = pipeline.run()

        assert result["embedded"] == 1
        assert result["failed"] == 0

    @pytest.mark.unit
    def test_run_with_force_reembeds(self) -> None:
        """Force mode clears existing vectors and re-embeds."""
        db = _make_test_db()
        db.execute(
            "INSERT INTO documents (hash, path, title, collection) VALUES (?, ?, ?, ?)",
            ("h1", "/docs/test.md", "Test", "default"),
        )
        db.execute("INSERT INTO content (hash, doc) VALUES (?, ?)", ("h1", "Test doc."))
        db.execute(
            "INSERT INTO content_vectors (hash, seq, pos, model, embedded_at) VALUES (?, ?, ?, ?, ?)",
            ("h1", 0, 0, "test", 1000),
        )
        db.commit()

        pipeline = EmbedPipeline(db=db, deps=_make_fake_deps())
        result = pipeline.run(force=True)

        assert result["embedded"] == 1

    @pytest.mark.unit
    def test_run_respects_limit(self) -> None:
        """Limit caps the number of chunks processed."""
        db = _make_test_db()
        for i in range(5):
            db.execute(
                "INSERT INTO documents (hash, path, title, collection) VALUES (?, ?, ?, ?)",
                (f"h{i}", f"/docs/test{i}.md", f"Test {i}", "default"),
            )
            db.execute(
                "INSERT INTO content (hash, doc) VALUES (?, ?)",
                (f"h{i}", f"Document {i} content."),
            )
        db.commit()

        pipeline = EmbedPipeline(db=db, deps=_make_fake_deps())
        result = pipeline.run(limit=2)

        assert result["embedded"] == 2
