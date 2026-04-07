"""
Tests for mnemosyne.summaries.staleness
"""

import sqlite3
from pathlib import Path

import pytest

from mnemosyne.summaries.generate import SummaryResult
from mnemosyne.summaries.staleness import (
    get_stale_paths,
    get_summary,
    init_summaries_db,
    is_stale,
    write_summary,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    init_summaries_db(conn)
    yield conn
    conn.close()


@pytest.fixture
def sample_file(tmp_path: Path) -> Path:
    f = tmp_path / "doc.md"
    f.write_text("Hello world.")
    return f


def _make_result(path: str, l0: str = "Abstract.", l1: str | None = None) -> SummaryResult:
    return SummaryResult(
        path=path,
        l0=l0,
        l1=l1,
        model="gpt-4o-mini",
        generated_at="2025-01-01T00:00:00+00:00",
        tokens_used=10,
    )


# ---------------------------------------------------------------------------
# init_summaries_db
# ---------------------------------------------------------------------------


def test_init_creates_table(db: sqlite3.Connection):
    """init_summaries_db() should create the summaries table."""
    tables = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='summaries'").fetchall()
    assert len(tables) == 1, "summaries table not created"


def test_init_is_idempotent(db: sqlite3.Connection):
    """Calling init_summaries_db() twice should not raise."""
    init_summaries_db(db)  # second call
    tables = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='summaries'").fetchall()
    assert len(tables) == 1


# ---------------------------------------------------------------------------
# is_stale
# ---------------------------------------------------------------------------


def test_is_stale_true_for_missing_path(db: sqlite3.Connection, tmp_path: Path):
    """is_stale() returns True when no summary exists for the path."""
    path = str(tmp_path / "nonexistent.md")
    assert is_stale(path, db) is True


def test_is_stale_false_for_fresh_summary(db: sqlite3.Connection, sample_file: Path):
    """is_stale() returns False when summary mtime matches source mtime."""
    result = _make_result(str(sample_file))
    write_summary(result, db)
    # File has not been modified since write_summary captured its mtime
    assert is_stale(str(sample_file), db) is False


def test_is_stale_true_when_source_newer(db: sqlite3.Connection, sample_file: Path):
    """is_stale() returns True when source file is newer than stored mtime."""
    result = _make_result(str(sample_file))
    write_summary(result, db)

    # Artificially set stored mtime to past
    old_mtime = sample_file.stat().st_mtime - 10
    db.execute(
        "UPDATE summaries SET source_mtime = ? WHERE path = ?",
        (old_mtime, str(sample_file)),
    )
    db.commit()

    assert is_stale(str(sample_file), db) is True


# ---------------------------------------------------------------------------
# write_summary + get_summary round-trip
# ---------------------------------------------------------------------------


def test_write_and_get_summary_roundtrip(db: sqlite3.Connection, sample_file: Path):
    """write_summary() + get_summary() should persist and retrieve correctly."""
    result = _make_result(str(sample_file), l0="My abstract.", l1="My overview.")
    write_summary(result, db)

    retrieved = get_summary(str(sample_file), db)
    assert retrieved is not None
    assert retrieved.path == str(sample_file)
    assert retrieved.l0 == "My abstract."
    assert retrieved.l1 == "My overview."
    assert retrieved.model == "gpt-4o-mini"


def test_write_summary_upserts(db: sqlite3.Connection, sample_file: Path):
    """Writing a summary twice should update, not duplicate."""
    r1 = _make_result(str(sample_file), l0="First abstract.")
    write_summary(r1, db)

    r2 = _make_result(str(sample_file), l0="Updated abstract.")
    write_summary(r2, db)

    count = db.execute("SELECT COUNT(*) FROM summaries").fetchone()[0]
    assert count == 1

    retrieved = get_summary(str(sample_file), db)
    assert retrieved is not None
    assert retrieved.l0 == "Updated abstract."


def test_get_summary_returns_none_for_missing(db: sqlite3.Connection):
    """get_summary() returns None when path not in DB."""
    result = get_summary("/nonexistent/path.md", db)
    assert result is None


# ---------------------------------------------------------------------------
# get_stale_paths
# ---------------------------------------------------------------------------


def test_get_stale_paths_returns_missing(db: sqlite3.Connection, tmp_path: Path):
    """get_stale_paths() includes paths with no summary."""
    p1 = str(tmp_path / "a.md")
    p2 = str(tmp_path / "b.md")
    Path(p1).write_text("a")
    Path(p2).write_text("b")

    # Write summary for p1 only
    write_summary(_make_result(p1), db)

    stale = get_stale_paths([p1, p2], db)
    assert p2 in stale
    assert p1 not in stale
