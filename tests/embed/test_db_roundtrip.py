"""
Integration tests: DB schema validation, vector insert/query roundtrip.
Uses a real (temporary) SQLite DB with the QMD schema. No Azure calls.
"""

import sqlite3
import time

import pytest

from kairix.embed.embed import stage_embedding
from kairix.embed.schema import (
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


# ── Schema validation tests ───────────────────────────────────────────────────


@pytest.mark.contract
@pytest.mark.integration
class TestSchemaValidation:
    @pytest.mark.unit
    def test_valid_schema_passes(self, tmp_db):
        validate_schema(tmp_db)  # Should not raise
        assert True, "smoke: valid schema accepted without error"

    @pytest.mark.unit
    def test_missing_content_vectors_column_raises(self, tmp_db):
        tmp_db.execute("DROP TABLE content_vectors")
        tmp_db.execute("CREATE TABLE content_vectors (hash TEXT PRIMARY KEY)")
        tmp_db.commit()
        with pytest.raises(SchemaVersionError, match="missing columns"):
            validate_schema(tmp_db)

    @pytest.mark.unit
    def test_missing_content_column_raises(self, tmp_db):
        tmp_db.execute("DROP TABLE content")
        tmp_db.execute("CREATE TABLE content (hash TEXT PRIMARY KEY)")
        tmp_db.commit()
        with pytest.raises(SchemaVersionError, match="missing columns"):
            validate_schema(tmp_db)


# ── Vec table tests ───────────────────────────────────────────────────────────


@pytest.mark.unit
class TestEnsureVecTable:
    @pytest.mark.unit
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

    @pytest.mark.unit
    def test_idempotent_same_dims(self, tmp_db):
        try:
            ensure_vec_table(tmp_db, 4)
            ensure_vec_table(tmp_db, 4)  # Second call should be no-op
        except sqlite3.OperationalError as e:
            if "no such module: vec0" in str(e):
                pytest.skip("sqlite-vec not available")
            raise
        assert True, "smoke: idempotent call did not raise"


# ── Insert embedding tests ────────────────────────────────────────────────────


@pytest.mark.unit
class TestInsertEmbedding:
    @pytest.mark.unit
    def test_stage_embedding_inserts_to_content_vectors(self, tmp_db):
        """stage_embedding writes directly to content_vectors."""
        vec = [0.1, 0.2, 0.3, 0.4]
        stage_embedding(tmp_db, "testhash", 0, 0, vec, "test-model", int(time.time()))
        tmp_db.commit()

        row = tmp_db.execute("SELECT hash, seq, model FROM content_vectors WHERE hash='testhash'").fetchone()
        assert row is not None
        assert row[0] == "testhash"
        assert row[1] == 0

    @pytest.mark.unit
    def test_idempotent_insert(self, tmp_db):
        """Duplicate stage_embedding calls for the same hash+seq replace, not duplicate."""
        vec = [0.1, 0.2, 0.3, 0.4]
        stage_embedding(tmp_db, "h1", 0, 0, vec, "model", 100)
        stage_embedding(tmp_db, "h1", 0, 0, vec, "model", 200)  # same hash
        tmp_db.commit()

        count = tmp_db.execute("SELECT COUNT(*) FROM content_vectors WHERE hash='h1'").fetchone()[0]
        assert count == 1
