"""
Integration tests for sqlite-vec runtime constraints.

These test the specific failure modes discovered during production deployment:

1. Extension must be loaded before ANY vec0 table operation (including DELETE)
2. sqlite-vec does not support INSERT OR REPLACE — requires DELETE then INSERT
3. qmd vsearch loads llama.cpp and hangs on CPU-only hosts — recall gate
   must use direct SQLite vector queries

Tests are skipped if sqlite-vec is not installed (expected in most dev environments).
In production the extension is present at the QMD node_modules path.
"""

import sqlite3
import struct

import pytest


# Try to locate and load sqlite-vec for these tests
def _find_vec_extension() -> str | None:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parents[2]))
    try:
        from kairix.embed.schema import find_sqlite_vec

        return find_sqlite_vec()
    except Exception:
        return None


VEC_EXT = _find_vec_extension()
needs_sqlite_vec = pytest.mark.skipif(
    VEC_EXT is None, reason="sqlite-vec not found (expected in dev; present in QMD runtime)"
)


def make_vec_db(dims: int = 4) -> sqlite3.Connection:
    """Create an in-memory DB with sqlite-vec loaded and a vectors_vec table."""
    db = sqlite3.connect(":memory:")
    db.enable_load_extension(True)
    db.load_extension(VEC_EXT.removesuffix(".so"))
    db.enable_load_extension(False)
    db.execute(
        f"CREATE VIRTUAL TABLE vectors_vec USING vec0("
        f"hash_seq TEXT PRIMARY KEY, embedding float[{dims}] distance_metric=cosine)"
    )
    db.commit()
    return db


def pack(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


# ── Constraint 1: Extension must be loaded before vec0 operations ──────────────


@pytest.mark.integration
@pytest.mark.contract
class TestExtensionLoadOrder:
    @needs_sqlite_vec
    @pytest.mark.unit
    def test_vec0_table_fails_without_extension(self):
        """
        Creating or querying a vec0 virtual table without loading the extension
        raises OperationalError: no such module: vec0.
        This is the failure mode that bit us on --force DELETE.
        """
        db = sqlite3.connect(":memory:")
        with pytest.raises(sqlite3.OperationalError, match="no such module: vec0"):
            db.execute("CREATE VIRTUAL TABLE t USING vec0(embedding float[4] distance_metric=cosine)")

    @needs_sqlite_vec
    @pytest.mark.unit
    def test_delete_on_vec0_fails_without_extension(self):
        """
        Even DELETE on an existing vec0 table fails if the extension isn't loaded
        in the current connection. This is the exact bug: --force ran DELETE FROM
        vectors_vec before load_sqlite_vec() was called.
        """
        # Set up a DB file with the extension loaded, write a row, close
        import os
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
            path = f.name
        try:
            db1 = sqlite3.connect(path)
            db1.enable_load_extension(True)
            db1.load_extension(VEC_EXT.removesuffix(".so"))
            db1.enable_load_extension(False)
            db1.execute(
                "CREATE VIRTUAL TABLE vectors_vec USING vec0("
                "hash_seq TEXT PRIMARY KEY, embedding float[4] distance_metric=cosine)"
            )
            db1.execute("INSERT INTO vectors_vec VALUES (?, ?)", ("h_0", pack([0.1, 0.2, 0.3, 0.4])))
            db1.commit()
            db1.close()

            # Re-open WITHOUT loading the extension
            db2 = sqlite3.connect(path)
            with pytest.raises(sqlite3.OperationalError, match="no such module: vec0"):
                db2.execute("DELETE FROM vectors_vec")
            db2.close()
        finally:
            os.unlink(path)

    @needs_sqlite_vec
    @pytest.mark.unit
    def test_load_sqlite_vec_enables_vec0(self):
        """load_sqlite_vec() makes vec0 operations available."""
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).parents[2]))
        from kairix.embed.schema import load_sqlite_vec

        db = sqlite3.connect(":memory:")
        load_sqlite_vec(db)
        # Should not raise
        db.execute(
            "CREATE VIRTUAL TABLE t USING vec0(hash_seq TEXT PRIMARY KEY, embedding float[4] distance_metric=cosine)"
        )


# ── Constraint 2: INSERT OR REPLACE not supported by sqlite-vec ───────────────


@pytest.mark.integration
@pytest.mark.contract
class TestSqliteVecInsertConstraints:
    @needs_sqlite_vec
    @pytest.mark.unit
    def test_insert_or_replace_raises(self):
        """
        sqlite-vec virtual tables reject INSERT OR REPLACE.
        This causes UNIQUE constraint failures when re-embedding
        chunks that already have vectors.
        """
        db = make_vec_db()
        db.execute("INSERT INTO vectors_vec VALUES (?, ?)", ("h_0", pack([0.1, 0.2, 0.3, 0.4])))
        db.commit()

        # INSERT OR REPLACE should fail (sqlite-vec limitation)
        with pytest.raises(sqlite3.OperationalError):
            db.execute("INSERT OR REPLACE INTO vectors_vec VALUES (?, ?)", ("h_0", pack([0.5, 0.6, 0.7, 0.8])))

    @needs_sqlite_vec
    @pytest.mark.unit
    def test_insert_or_ignore_raises(self):
        """sqlite-vec also rejects INSERT OR IGNORE."""
        db = make_vec_db()
        db.execute("INSERT INTO vectors_vec VALUES (?, ?)", ("h_0", pack([0.1] * 4)))
        db.commit()

        with pytest.raises(sqlite3.OperationalError):
            db.execute("INSERT OR IGNORE INTO vectors_vec VALUES (?, ?)", ("h_0", pack([0.5] * 4)))

    @needs_sqlite_vec
    @pytest.mark.unit
    def test_staging_upsert_is_idempotent(self):
        """
        The staging pattern (write to temp table, bulk merge into vec0) is idempotent.
        Writing the same hash_seq twice produces exactly one row in vectors_vec.
        """
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).parents[2]))
        from kairix.embed.embed import ensure_staging_table, flush_staging_to_vec

        db = make_vec_db()
        ensure_staging_table(db)

        # First write
        db.execute("INSERT OR REPLACE INTO vec_staging VALUES (?, ?)", ("h_0", pack([0.1, 0.2, 0.3, 0.4])))
        flush_staging_to_vec(db)
        db.commit()

        # Second write — same key, different vector
        db.execute("INSERT OR REPLACE INTO vec_staging VALUES (?, ?)", ("h_0", pack([0.5, 0.6, 0.7, 0.8])))
        flush_staging_to_vec(db)
        db.commit()

        count = db.execute("SELECT COUNT(*) FROM vectors_vec WHERE hash_seq='h_0'").fetchone()[0]
        assert count == 1

        row = db.execute("SELECT embedding FROM vectors_vec WHERE hash_seq='h_0'").fetchone()
        decoded = list(struct.unpack("<4f", row[0]))
        assert decoded[0] == pytest.approx(0.5, abs=1e-5)  # second write wins

    @needs_sqlite_vec
    @pytest.mark.unit
    def test_stage_and_flush_end_to_end(self):
        """
        stage_embedding() + flush_staging_to_vec() writes correctly to both
        content_vectors and vectors_vec. Calling twice with same hash_seq succeeds.
        """
        import sys
        import time
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).parents[2]))
        from kairix.embed.embed import ensure_staging_table, flush_staging_to_vec, stage_embedding
        from kairix.embed.schema import ensure_vec_table, load_sqlite_vec

        db = sqlite3.connect(":memory:")
        load_sqlite_vec(db)
        ensure_vec_table(db, 4)
        ensure_staging_table(db)
        db.execute("""
            CREATE TABLE content_vectors (
                hash TEXT, seq INTEGER, pos INTEGER, model TEXT, embedded_at TEXT,
                chunk_date TEXT,
                PRIMARY KEY (hash, seq)
            )
        """)
        db.commit()

        vec = [0.1, 0.2, 0.3, 0.4]
        now = int(time.time())

        with db:
            stage_embedding(db, "testhash", 0, 0, vec, "test-model", now)
            flush_staging_to_vec(db)

        # Second write — must not raise
        with db:
            stage_embedding(db, "testhash", 0, 0, vec, "test-model", now + 1)
            flush_staging_to_vec(db)

        cv_count = db.execute("SELECT COUNT(*) FROM content_vectors WHERE hash='testhash'").fetchone()[0]
        vv_count = db.execute("SELECT COUNT(*) FROM vectors_vec WHERE hash_seq='testhash_0'").fetchone()[0]
        staging_count = db.execute("SELECT COUNT(*) FROM vec_staging").fetchone()[0]

        assert cv_count == 1  # content_vectors: one row
        assert vv_count == 1  # vectors_vec: one row
        assert staging_count == 0  # staging: cleared after flush

    @needs_sqlite_vec
    @pytest.mark.unit
    def test_flush_clears_staging(self):
        """Staging table is empty after flush — ready for the next batch."""
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).parents[2]))
        from kairix.embed.embed import ensure_staging_table, flush_staging_to_vec

        db = make_vec_db()
        ensure_staging_table(db)

        for i in range(5):
            db.execute("INSERT OR REPLACE INTO vec_staging VALUES (?, ?)", (f"h_{i}_0", pack([float(i)] * 4)))

        flush_staging_to_vec(db)
        db.commit()

        remaining = db.execute("SELECT COUNT(*) FROM vec_staging").fetchone()[0]
        assert remaining == 0

        in_vec = db.execute("SELECT COUNT(*) FROM vectors_vec").fetchone()[0]
        assert in_vec == 5


# ── Constraint 3: Direct vector search (no llama.cpp dependency) ──────────────


@pytest.mark.unit
class TestDirectVectorSearch:
    @needs_sqlite_vec
    @pytest.mark.unit
    def test_match_query_returns_results(self):
        """
        Direct MATCH query against vectors_vec returns nearest neighbours
        without any llama.cpp/qmd vsearch involvement.
        """
        db = make_vec_db(dims=4)

        # Insert three vectors
        db.execute("INSERT INTO vectors_vec VALUES (?, ?)", ("doc_a_0", pack([1.0, 0.0, 0.0, 0.0])))
        db.execute("INSERT INTO vectors_vec VALUES (?, ?)", ("doc_b_0", pack([0.0, 1.0, 0.0, 0.0])))
        db.execute("INSERT INTO vectors_vec VALUES (?, ?)", ("doc_c_0", pack([0.0, 0.0, 1.0, 0.0])))
        db.commit()

        # Query closest to [1, 0, 0, 0] — should return doc_a first
        query_vec = pack([1.0, 0.0, 0.0, 0.0])
        rows = db.execute(
            "SELECT hash_seq, distance FROM vectors_vec WHERE embedding MATCH ? AND k = 3 ORDER BY distance",
            (query_vec,),
        ).fetchall()

        assert len(rows) == 3
        assert rows[0][0] == "doc_a_0"  # closest
        assert rows[0][1] == pytest.approx(0.0, abs=1e-5)  # cosine distance 0 = identical

    @needs_sqlite_vec
    @pytest.mark.unit
    def test_match_query_cosine_ordering(self):
        """Cosine distance orders results correctly — lower distance = more similar."""
        db = make_vec_db(dims=4)

        db.execute("INSERT INTO vectors_vec VALUES (?, ?)", ("near_0", pack([0.9, 0.1, 0.0, 0.0])))
        db.execute("INSERT INTO vectors_vec VALUES (?, ?)", ("mid_0", pack([0.5, 0.5, 0.0, 0.0])))
        db.execute("INSERT INTO vectors_vec VALUES (?, ?)", ("far_0", pack([0.0, 0.0, 1.0, 0.0])))
        db.commit()

        query_vec = pack([1.0, 0.0, 0.0, 0.0])
        rows = db.execute(
            "SELECT hash_seq FROM vectors_vec WHERE embedding MATCH ? AND k = 3 ORDER BY distance", (query_vec,)
        ).fetchall()

        assert [r[0] for r in rows] == ["near_0", "mid_0", "far_0"]

    @needs_sqlite_vec
    @pytest.mark.unit
    def test_recall_check_uses_direct_search(self):
        """
        recall_check.py check_recall() uses direct SQLite vector search,
        not qmd vsearch. Verify it doesn't attempt to call qmd binary.
        """
        import sys
        from pathlib import Path
        from unittest.mock import patch

        sys.path.insert(0, str(Path(__file__).parents[2]))
        from kairix.embed.recall_check import _vsearch_direct

        db = make_vec_db(dims=4)

        # Create a minimal documents + content_vectors scaffold
        db.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY, collection TEXT, path TEXT,
                title TEXT, hash TEXT, active INTEGER DEFAULT 1
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS content_vectors (
                hash TEXT, seq INTEGER, pos INTEGER, model TEXT, embedded_at TEXT,
                PRIMARY KEY (hash, seq)
            )
        """)
        db.execute("INSERT INTO documents (collection, path, hash) VALUES ('test', 'voice/profile.md', 'h1')")
        db.execute(
            "INSERT INTO content_vectors (hash, seq, pos, model, embedded_at) VALUES ('h1', 0, 0, 'test', 'now')"
        )
        db.execute("INSERT INTO vectors_vec VALUES ('h1_0', ?)", (pack([1.0, 0.0, 0.0, 0.0]),))
        db.commit()

        query_vec = pack([1.0, 0.0, 0.0, 0.0])
        results = _vsearch_direct(db, query_vec, limit=3)

        assert "voice/profile.md" in results

        # Crucially: subprocess was never called (qmd binary not invoked)
        with patch("subprocess.run") as mock_run:
            _vsearch_direct(db, query_vec, limit=3)
            mock_run.assert_not_called()
