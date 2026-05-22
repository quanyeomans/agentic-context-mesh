"""
Integration tests: DB schema validation, vector insert/query roundtrip.
Uses a real (temporary) SQLite DB with the kairix schema. No Azure calls.
"""

import sqlite3
import time

import pytest

from kairix.core.embed.embed import stage_embedding
from kairix.core.embed.schema import SchemaVersionError, validate_schema

pytestmark = pytest.mark.integration

# ── Fixtures ──────────────────────────────────────────────────────────────────


def create_kairix_schema(db: sqlite3.Connection) -> None:
    """Create the minimum kairix schema needed for our tests.

    Includes the connector-framework Wave 1 (SC-4) tables + ``sensitivity``
    column so ``validate_schema`` accepts the DB.
    """
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
            active INTEGER NOT NULL DEFAULT 1,
            sensitivity TEXT NOT NULL DEFAULT 'public'
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

        -- Connector-framework Wave 1 (SC-4) tables required by validate_schema
        CREATE TABLE IF NOT EXISTS documents_media (
            hash TEXT PRIMARY KEY,
            path TEXT NOT NULL,
            format TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS document_pages (
            hash TEXT NOT NULL,
            page_number INTEGER NOT NULL,
            PRIMARY KEY (hash, page_number)
        );
        CREATE TABLE IF NOT EXISTS connector_cursors (
            source_name TEXT PRIMARY KEY,
            cursor_token TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS connector_deadletter (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_name TEXT NOT NULL,
            item_id TEXT NOT NULL,
            failure_count INTEGER NOT NULL,
            last_attempt TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS bronze_records (
            source_name TEXT NOT NULL,
            item_id TEXT NOT NULL,
            raw_path TEXT NOT NULL,
            mime TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            PRIMARY KEY (source_name, item_id)
        );
        CREATE TABLE IF NOT EXISTS entity_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            value TEXT NOT NULL,
            source_uri TEXT NOT NULL,
            modified_at TEXT NOT NULL,
            confidence REAL NOT NULL,
            sensitivity TEXT NOT NULL
        );
    """)
    db.commit()


@pytest.fixture
def tmp_db():
    """Provide a fresh in-memory kairix-schema SQLite DB."""
    db = sqlite3.connect(":memory:")
    db.execute("PRAGMA journal_mode=WAL")
    create_kairix_schema(db)
    yield db
    db.close()


# ── Schema validation tests ───────────────────────────────────────────────────


@pytest.mark.contract
@pytest.mark.integration
class TestSchemaValidation:
    @pytest.mark.integration
    def test_valid_schema_passes(self, tmp_db):
        # Contract: validate_schema returns None and does not raise on a
        # well-formed schema. The lack of exception IS the test — but pin
        # the documented return type so a future refactor can't silently
        # change the contract (replaces a tautological ``assert True``; S5914).
        assert validate_schema(tmp_db) is None

    @pytest.mark.integration
    def test_missing_content_vectors_column_raises(self, tmp_db):
        tmp_db.execute("DROP TABLE content_vectors")
        tmp_db.execute("CREATE TABLE content_vectors (hash TEXT PRIMARY KEY)")
        tmp_db.commit()
        with pytest.raises(SchemaVersionError, match="missing columns"):
            validate_schema(tmp_db)

    @pytest.mark.integration
    def test_missing_content_column_raises(self, tmp_db):
        tmp_db.execute("DROP TABLE content")
        tmp_db.execute("CREATE TABLE content (hash TEXT PRIMARY KEY)")
        tmp_db.commit()
        with pytest.raises(SchemaVersionError, match="missing columns"):
            validate_schema(tmp_db)


# ── Insert embedding tests ────────────────────────────────────────────────────


class TestInsertEmbedding:
    @pytest.mark.integration
    def test_stage_embedding_inserts_to_content_vectors(self, tmp_db):
        """stage_embedding writes directly to content_vectors."""
        vec = [0.1, 0.2, 0.3, 0.4]
        stage_embedding(tmp_db, "testhash", 0, 0, vec, "test-model", int(time.time()))
        tmp_db.commit()

        row = tmp_db.execute("SELECT hash, seq, model FROM content_vectors WHERE hash='testhash'").fetchone()
        assert row is not None
        assert row[0] == "testhash"
        assert row[1] == 0

    @pytest.mark.integration
    def test_idempotent_insert(self, tmp_db):
        """Duplicate stage_embedding calls for the same hash+seq replace, not duplicate."""
        vec = [0.1, 0.2, 0.3, 0.4]
        stage_embedding(tmp_db, "h1", 0, 0, vec, "model", 100)
        stage_embedding(tmp_db, "h1", 0, 0, vec, "model", 200)  # same hash
        tmp_db.commit()

        count = tmp_db.execute("SELECT COUNT(*) FROM content_vectors WHERE hash='h1'").fetchone()[0]
        assert count == 1
