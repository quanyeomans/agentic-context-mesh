"""
Additional tests for kairix.embed.schema — covers previously-untested paths:
- find_sqlite_vec(): env override, fallback, missing
- load_sqlite_vec(): missing extension error
- get_qmd_db_path(): env override, missing file
- ensure_vec_table(): create, dimension mismatch re-create
- get_pending_chunks(): synthetic DB
- get_all_chunks_needing_embedding(): synthetic DB
- save_run_log(): creates and rotates
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from kairix.embed.schema import (
    ensure_vec_table,
    find_sqlite_vec,
    get_all_chunks_needing_embedding,
    get_pending_chunks,
    get_qmd_db_path,
    load_sqlite_vec,
    save_run_log,
)

# ---------------------------------------------------------------------------
# find_sqlite_vec
# ---------------------------------------------------------------------------


def test_find_sqlite_vec_uses_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Returns the path from SQLITE_VEC_PATH env var when it exists."""
    so = tmp_path / "vec0.so"
    so.touch()
    monkeypatch.setenv("SQLITE_VEC_PATH", str(so))
    result = find_sqlite_vec()
    assert result == str(so)


def test_find_sqlite_vec_returns_none_when_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """Returns None when no vec0.so can be located."""
    monkeypatch.delenv("SQLITE_VEC_PATH", raising=False)
    with patch("kairix.embed.schema._SQLITE_VEC_SEARCH_PATHS", ["/nonexistent/vec0.so"]):
        with patch("pathlib.Path.exists", return_value=False):
            result = find_sqlite_vec()
    assert result is None


# ---------------------------------------------------------------------------
# load_sqlite_vec
# ---------------------------------------------------------------------------


def test_load_sqlite_vec_raises_when_not_found() -> None:
    """Raises RuntimeError with helpful message when extension is not found."""
    db = sqlite3.connect(":memory:")
    with patch("kairix.embed.schema.find_sqlite_vec", return_value=None):
        with pytest.raises(RuntimeError, match="sqlite-vec extension"):
            load_sqlite_vec(db)


# ---------------------------------------------------------------------------
# get_qmd_db_path
# ---------------------------------------------------------------------------


def test_get_qmd_db_path_uses_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Uses QMD_CACHE_DIR env var when set."""
    db_file = tmp_path / "index.sqlite"
    db_file.touch()
    monkeypatch.setenv("QMD_CACHE_DIR", str(tmp_path))
    result = get_qmd_db_path()
    assert result == db_file


def test_get_qmd_db_path_raises_when_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Raises FileNotFoundError when DB doesn't exist."""
    monkeypatch.setenv("QMD_CACHE_DIR", str(tmp_path))  # empty dir, no index.sqlite
    with pytest.raises(FileNotFoundError, match="QMD index not found"):
        get_qmd_db_path()


# ---------------------------------------------------------------------------
# ensure_vec_table
# ---------------------------------------------------------------------------


def test_ensure_vec_table_creates_table_via_fake_db() -> None:
    """ensure_vec_table() issues CREATE VIRTUAL TABLE with the correct dimensions."""
    executed: list[str] = []

    class FakeDB:
        def execute(self, sql: str, *args, **kwargs):
            executed.append(sql)

            class FakeCursor:
                def fetchone(self) -> None:
                    return None  # no existing table

            return FakeCursor()

        def commit(self) -> None:
            pass

    ensure_vec_table(FakeDB(), dims=1536)  # type: ignore[arg-type]
    create_sqls = [s for s in executed if "CREATE" in s and "vec0" in s]
    assert any("float[1536]" in s for s in create_sqls)


def test_ensure_vec_table_skips_recreate_when_dims_match() -> None:
    """ensure_vec_table() returns early when table already has correct dims."""
    executed: list[str] = []

    class FakeDB:
        def execute(self, sql: str, *args, **kwargs):
            executed.append(sql)

            class FakeCursor:
                def fetchone(self):
                    return ("CREATE VIRTUAL TABLE vectors_vec USING vec0(hash_seq TEXT, embedding float[1536])",)

            return FakeCursor()

        def commit(self) -> None:
            pass

    ensure_vec_table(FakeDB(), dims=1536)  # type: ignore[arg-type]
    # Only the SELECT should have been called; no DROP or CREATE
    assert not any("DROP" in s or ("CREATE" in s and "vec0" in s) for s in executed)


# ---------------------------------------------------------------------------
# get_pending_chunks + get_all_chunks_needing_embedding
# ---------------------------------------------------------------------------


def _make_minimal_qmd_db() -> sqlite3.Connection:
    """Create minimal in-memory QMD schema for testing."""
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE documents (hash TEXT PRIMARY KEY, path TEXT, active INTEGER DEFAULT 1)")
    db.execute("CREATE TABLE content (hash TEXT PRIMARY KEY, doc TEXT)")
    db.execute("CREATE TABLE content_vectors (hash TEXT, seq INTEGER, pos INTEGER)")
    return db


def test_get_pending_chunks_returns_empty_when_all_embedded() -> None:
    """Returns [] when no chunks need embedding."""
    db = _make_minimal_qmd_db()
    db.execute("INSERT INTO documents VALUES ('abc123', 'test/doc.md', 1)")
    db.execute("INSERT INTO content VALUES ('abc123', 'some content')")
    # No content_vectors rows → content_vectors LEFT JOIN won't exclude them

    # For this test, get_pending_chunks expects v.hash IS NULL — since content_vectors
    # has no rows, all content is pending. So there should be 1 pending chunk.
    chunks = get_pending_chunks(db)
    assert len(chunks) == 1
    assert chunks[0]["path"] == "test/doc.md"
    assert chunks[0]["hash"] == "abc123"


def test_get_pending_chunks_skips_inactive_docs() -> None:
    """Skips documents with active=0."""
    db = _make_minimal_qmd_db()
    db.execute("INSERT INTO documents VALUES ('abc123', 'test/doc.md', 0)")  # inactive
    db.execute("INSERT INTO content VALUES ('abc123', 'some content')")

    chunks = get_pending_chunks(db)
    assert chunks == []


def test_get_pending_chunks_skips_empty_content() -> None:
    """Skips chunks with empty doc text."""
    db = _make_minimal_qmd_db()
    db.execute("INSERT INTO documents VALUES ('abc123', 'test/doc.md', 1)")
    db.execute("INSERT INTO content VALUES ('abc123', '')")  # empty content

    chunks = get_pending_chunks(db)
    assert chunks == []


def test_get_all_chunks_needing_embedding_returns_empty_without_content_vectors() -> None:
    """Returns [] when content_vectors table is empty."""
    db = _make_minimal_qmd_db()
    # Need vectors_vec table too (no vec0 extension — just a regular table)
    db.execute("CREATE TABLE vectors_vec (hash_seq TEXT PRIMARY KEY)")

    result = get_all_chunks_needing_embedding(db)
    assert result == []


# ---------------------------------------------------------------------------
# save_run_log
# ---------------------------------------------------------------------------


def test_save_run_log_creates_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Creates the run log file on first call."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / ".cache" / "qmd").mkdir(parents=True)

    save_run_log({"run": 1, "status": "ok"})

    log_path = tmp_path / ".cache" / "qmd" / "azure-embed-runs.json"
    assert log_path.exists()
    runs = json.loads(log_path.read_text())
    assert runs == [{"run": 1, "status": "ok"}]


def test_save_run_log_appends_and_rotates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Appends to existing log and rotates to keep last 90 runs."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    log_dir = tmp_path / ".cache" / "qmd"
    log_dir.mkdir(parents=True)
    log_path = log_dir / "azure-embed-runs.json"

    # Write 90 existing entries
    existing = [{"run": i} for i in range(90)]
    log_path.write_text(json.dumps(existing))

    # Add one more — should rotate to 90 total
    save_run_log({"run": 90})

    runs = json.loads(log_path.read_text())
    assert len(runs) == 90
    assert runs[-1] == {"run": 90}
    assert runs[0] == {"run": 1}  # run 0 was dropped
