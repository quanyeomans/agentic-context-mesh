"""
Integration tests: DB schema validation, vector insert/query roundtrip.
Uses a real (temporary) SQLite DB with the QMD schema. No Azure calls.
"""

import sqlite3
import time
from unittest.mock import patch

import pytest

from kairix.embed.embed import ensure_staging_table, flush_staging_to_vec, stage_embedding
from kairix.embed.schema import (
    EMBED_VECTOR_DIMS,
    SchemaVersionError,
    ensure_vec_table,
    validate_schema,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


def create_qmd_schema(db: sqlite3.Connection) -> None:
    """Create the minimum QMD schema needed for our tests (mirrors qmd@1.1.2)."""
    db.executescript("""
        CREATE TABLE IF NOT EXISTS content (
            hash TEXT PRIMARY KEY,
            doc TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY,
            collection TEXT NOT NULL,
            path TEXT NOT NULL,
            title TEXT,
            hash TEXT NOT NULL,
            created_at TEXT,
            modified_at TEXT,
            active INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS content_vectors (
            hash TEXT NOT NULL,
            seq INTEGER NOT NULL DEFAULT 0,
            pos INTEGER NOT NULL DEFAULT 0,
            model TEXT NOT NULL,
            embedded_at TEXT NOT NULL,
            chunk_date DATE,
            PRIMARY KEY (hash, seq)
        );
    """)
    db.commit()


@pytest.fixture
def tmp_db():
    """Provide a fresh in-memory QMD-schema SQLite DB."""
    db = sqlite3.connect(":memory:")
    db.execute("PRAGMA journal_mode=WAL")
    create_qmd_schema(db)
    yield db
    db.close()


@pytest.fixture
def tmp_db_with_docs(tmp_db):
    """DB with a few sample documents (real qmd@1.1.2 schema)."""
    docs = [
        ("hash_a", "The quick brown fox jumps over the lazy dog. " * 10, "notes/test.md"),
        ("hash_b", "Azure OpenAI provides text embedding models. " * 5, "docs/azure.md"),
        ("hash_c", "QMD is a local search tool for markdown files. " * 8, "tools/qmd.md"),
    ]
    for h, doc, path in docs:
        tmp_db.execute("INSERT INTO content (hash, doc, created_at) VALUES (?,?,?)", (h, doc, "2026-01-01T00:00:00Z"))
        tmp_db.execute("INSERT INTO documents (collection, path, hash, active) VALUES (?,?,?,1)", ("test", path, h))
    tmp_db.commit()
    return tmp_db


# ── Schema validation tests ───────────────────────────────────────────────────


@pytest.mark.contract
@pytest.mark.integration
class TestSchemaValidation:
    def test_valid_schema_passes(self, tmp_db):
        validate_schema(tmp_db)  # Should not raise

    def test_missing_content_vectors_column_raises(self, tmp_db):
        tmp_db.execute("DROP TABLE content_vectors")
        tmp_db.execute("CREATE TABLE content_vectors (hash TEXT PRIMARY KEY)")
        tmp_db.commit()
        with pytest.raises(SchemaVersionError, match="missing columns"):
            validate_schema(tmp_db)

    def test_missing_content_column_raises(self, tmp_db):
        tmp_db.execute("DROP TABLE content")
        tmp_db.execute("CREATE TABLE content (hash TEXT PRIMARY KEY)")
        tmp_db.commit()
        with pytest.raises(SchemaVersionError, match="missing columns"):
            validate_schema(tmp_db)


# ── Vec table tests ───────────────────────────────────────────────────────────


class TestEnsureVecTable:
    def test_creates_table(self, tmp_db):
        # Requires sqlite-vec extension — skip if not available
        try:
            ensure_vec_table(tmp_db, 4)
        except sqlite3.OperationalError as e:
            if "no such module: vec0" in str(e):
                pytest.skip("sqlite-vec not available in test environment")
            raise

        tables = {r[0] for r in tmp_db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "vectors_vec" in tables

    def test_idempotent_same_dims(self, tmp_db):
        try:
            ensure_vec_table(tmp_db, 4)
            ensure_vec_table(tmp_db, 4)  # Second call should be no-op
        except sqlite3.OperationalError as e:
            if "no such module: vec0" in str(e):
                pytest.skip("sqlite-vec not available")
            raise


# ── Insert embedding tests ────────────────────────────────────────────────────


class TestInsertEmbedding:
    def test_inserts_to_content_vectors(self, tmp_db):
        try:
            ensure_vec_table(tmp_db, 4)
            ensure_staging_table(tmp_db)
        except sqlite3.OperationalError as e:
            if "no such module: vec0" in str(e):
                pytest.skip("sqlite-vec not available")
            raise

        vec = [0.1, 0.2, 0.3, 0.4]
        stage_embedding(tmp_db, "testhash", 0, 0, vec, "test-model", int(time.time()))
        flush_staging_to_vec(tmp_db)
        tmp_db.commit()

        row = tmp_db.execute("SELECT hash, seq, model FROM content_vectors WHERE hash='testhash'").fetchone()
        assert row is not None
        assert row[0] == "testhash"
        assert row[1] == 0

    def test_inserts_to_vectors_vec(self, tmp_db):
        try:
            ensure_vec_table(tmp_db, 4)
            ensure_staging_table(tmp_db)
        except sqlite3.OperationalError as e:
            if "no such module: vec0" in str(e):
                pytest.skip("sqlite-vec not available")
            raise

        vec = [0.1, 0.2, 0.3, 0.4]
        stage_embedding(tmp_db, "testhash", 0, 0, vec, "test-model", int(time.time()))
        flush_staging_to_vec(tmp_db)
        tmp_db.commit()

        row = tmp_db.execute("SELECT hash_seq FROM vectors_vec WHERE hash_seq='testhash_0'").fetchone()
        assert row is not None

    def test_idempotent_insert(self, tmp_db):
        try:
            ensure_vec_table(tmp_db, 4)
            ensure_staging_table(tmp_db)
        except sqlite3.OperationalError as e:
            if "no such module: vec0" in str(e):
                pytest.skip("sqlite-vec not available")
            raise

        vec = [0.1, 0.2, 0.3, 0.4]
        with tmp_db:
            stage_embedding(tmp_db, "h1", 0, 0, vec, "model", 100)
            flush_staging_to_vec(tmp_db)
        with tmp_db:
            stage_embedding(tmp_db, "h1", 0, 0, vec, "model", 200)  # same hash
            flush_staging_to_vec(tmp_db)

        count = tmp_db.execute("SELECT COUNT(*) FROM content_vectors WHERE hash='h1'").fetchone()[0]
        assert count == 1


# ── Full roundtrip test ───────────────────────────────────────────────────────


class TestFullRoundtrip:
    def test_pending_to_embedded(self, tmp_db_with_docs):
        """Simulate: pending chunks → mock Azure → inserted → nothing pending."""
        try:
            ensure_vec_table(tmp_db_with_docs, EMBED_VECTOR_DIMS)
        except sqlite3.OperationalError as e:
            if "no such module: vec0" in str(e):
                pytest.skip("sqlite-vec not available")
            raise

        fake_vec = [0.01] * EMBED_VECTOR_DIMS

        with patch(
            "qmd_azure_embed.embed._get_azure_config", return_value=("key", "https://test.azure.com", "deployment")
        ):
            with patch("qmd_azure_embed.embed.preflight_check", return_value=EMBED_VECTOR_DIMS):
                with patch("qmd_azure_embed.embed.embed_batch", return_value=[fake_vec] * 100):
                    from kairix.embed.embed import run_embed

                    result = run_embed(tmp_db_with_docs, force=False)

        assert result["embedded"] > 0
        assert result["failed"] == 0

        # Nothing should be pending now
        pending = tmp_db_with_docs.execute("""
            SELECT COUNT(*) FROM content c
            JOIN documents d ON d.hash = c.hash
            LEFT JOIN content_vectors v ON c.hash = v.hash AND v.seq = 0
            WHERE v.hash IS NULL AND d.active = 1
        """).fetchone()[0]
        assert pending == 0
