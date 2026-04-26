"""Unit test: verify embed progress logging fires during a mock embed run."""

import logging
import sqlite3
from unittest.mock import patch

import pytest

from kairix.embed.embed import run_embed

pytestmark = pytest.mark.unit


def _fake_vec(dims: int = 1536) -> list[float]:
    return [0.1] * dims


@pytest.fixture()
def embed_db(tmp_path):
    """Minimal SQLite DB with the tables run_embed expects."""
    db_path = tmp_path / "embed_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE documents (
            hash TEXT PRIMARY KEY,
            path TEXT,
            title TEXT,
            active INTEGER DEFAULT 1
        )
    """)
    conn.execute("""
        CREATE TABLE content (
            hash TEXT PRIMARY KEY,
            doc TEXT
        )
    """)
    # Insert two small docs so we get at least one batch
    for i in range(2):
        h = f"hash{i}"
        conn.execute("INSERT INTO documents (hash, path, active) VALUES (?, ?, 1)", (h, f"doc{i}.md"))
        conn.execute("INSERT INTO content (hash, doc) VALUES (?, ?)", (h, f"Body text number {i} for testing."))
    conn.execute("""
        CREATE TABLE content_vectors (
            hash TEXT,
            seq INTEGER,
            pos INTEGER,
            model TEXT,
            dims INTEGER,
            embedded_at INTEGER,
            chunk_date TEXT,
            PRIMARY KEY (hash, seq)
        )
    """)
    conn.execute("""
        CREATE TABLE vec_staging (
            hash TEXT,
            seq INTEGER,
            pos INTEGER,
            vector BLOB,
            model TEXT,
            dims INTEGER,
            embedded_at INTEGER,
            chunk_date TEXT,
            PRIMARY KEY (hash, seq)
        )
    """)
    conn.commit()
    yield conn
    conn.close()


def test_embed_progress_logging_emitted(embed_db, caplog):
    """run_embed must emit 'Embed progress:' log lines during batch processing."""
    dims = 1536

    with (
        patch("kairix.embed.embed._get_azure_config", return_value=("key", "https://endpoint", "deploy")),
        patch("kairix.embed.embed.load_sqlite_vec"),
        patch("kairix.embed.embed.preflight_check", return_value=dims),
        patch("kairix.embed.embed.ensure_vec_table"),
        patch("kairix.embed.embed.ensure_staging_table"),
        patch("kairix.embed.embed.migrate_content_vectors"),
        patch("kairix.embed.embed.embed_batch", return_value=[_fake_vec(dims)]),
        patch("kairix.embed.embed.stage_embedding"),
        patch("kairix.embed.embed.flush_staging_to_vec"),
    ):
        with caplog.at_level(logging.INFO, logger="kairix.embed.embed"):
            result = run_embed(embed_db, batch_size=50, limit=2)

    # Verify the progress log line was actually emitted
    progress_lines = [r for r in caplog.records if "Embed progress:" in r.message]
    assert len(progress_lines) >= 1, "Expected at least one 'Embed progress:' log record"
    assert result["embedded"] > 0
