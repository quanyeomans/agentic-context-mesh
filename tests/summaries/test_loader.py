"""
Tests for kairix.summaries.loader
"""

import sqlite3
from pathlib import Path

import pytest

from kairix.summaries.generate import SummaryResult
from kairix.summaries.loader import get_l0, get_l1, load_tiered_content
from kairix.summaries.staleness import init_summaries_db, write_summary

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
def doc_file(tmp_path: Path) -> Path:
    f = tmp_path / "doc.md"
    f.write_text("Full document content. " * 200)  # ~800 words worth
    return f


def _store_summary(
    db: sqlite3.Connection,
    path: str,
    l0: str = "L0 abstract.",
    l1: str | None = None,
) -> None:
    result = SummaryResult(
        path=path,
        l0=l0,
        l1=l1,
        model="gpt-4o-mini",
        generated_at="2025-01-01T00:00:00+00:00",
        tokens_used=10,
    )
    write_summary(result, db)


# ---------------------------------------------------------------------------
# get_l0 / get_l1 accessors
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_get_l0_returns_abstract(db: sqlite3.Connection, doc_file: Path):
    _store_summary(db, str(doc_file), l0="Short abstract here.")
    result = get_l0(str(doc_file), db)
    assert result == "Short abstract here."


@pytest.mark.unit
def test_get_l0_returns_none_when_absent(db: sqlite3.Connection, doc_file: Path):
    result = get_l0(str(doc_file), db)
    assert result is None


@pytest.mark.unit
def test_get_l1_returns_overview(db: sqlite3.Connection, doc_file: Path):
    _store_summary(db, str(doc_file), l0="Abstract.", l1="L1 overview content.")
    result = get_l1(str(doc_file), db)
    assert result == "L1 overview content."


@pytest.mark.unit
def test_get_l1_returns_none_when_absent(db: sqlite3.Connection, doc_file: Path):
    _store_summary(db, str(doc_file), l0="Abstract.", l1=None)
    result = get_l1(str(doc_file), db)
    assert result is None


# ---------------------------------------------------------------------------
# load_tiered_content — budget=100 (L0 preferred)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_load_tiered_returns_l0_when_budget_100_and_l0_exists(db: sqlite3.Connection, doc_file: Path):
    """budget=100 → L0 preferred when available."""
    _store_summary(db, str(doc_file), l0="Brief abstract.")
    content, tier = load_tiered_content(str(doc_file), db, budget_tokens=100)
    assert content == "Brief abstract."
    assert tier == "l0"


@pytest.mark.unit
def test_load_tiered_falls_back_to_truncated_when_no_summary(db: sqlite3.Connection, doc_file: Path):
    """No summary → truncated file content."""
    content, tier = load_tiered_content(str(doc_file), db, budget_tokens=100)
    assert tier == "truncated"
    # Should be capped at ~150 tokens = 600 chars
    assert len(content) <= 600 + 10  # slight tolerance


@pytest.mark.unit
def test_load_tiered_returns_l1_when_budget_400_and_l1_exists(db: sqlite3.Connection, doc_file: Path):
    """budget=400 → L1 preferred when available."""
    _store_summary(
        db,
        str(doc_file),
        l0="Abstract.",
        l1="Detailed L1 overview.",
    )
    content, tier = load_tiered_content(str(doc_file), db, budget_tokens=400)
    assert content == "Detailed L1 overview."
    assert tier == "l1"


@pytest.mark.unit
def test_load_tiered_falls_back_to_l0_when_budget_400_and_no_l1(db: sqlite3.Connection, doc_file: Path):
    """budget=400 with no L1 → falls back to L0."""
    _store_summary(db, str(doc_file), l0="Abstract only.", l1=None)
    content, tier = load_tiered_content(str(doc_file), db, budget_tokens=400)
    assert content == "Abstract only."
    assert tier == "l0"


@pytest.mark.unit
def test_load_tiered_returns_full_when_budget_high(db: sqlite3.Connection, doc_file: Path):
    """budget > 600 with short file → full content."""
    short_file = doc_file.parent / "short.md"
    short_file.write_text("Short file.")
    content, tier = load_tiered_content(str(short_file), db, budget_tokens=2000)
    assert content == "Short file."
    assert tier == "full"


@pytest.mark.unit
def test_load_tiered_truncates_large_file_at_high_budget(db: sqlite3.Connection, doc_file: Path):
    """budget > 600 with large file → truncated."""
    # doc_file has ~4400 chars, budget=700 = 2800 chars
    content, tier = load_tiered_content(str(doc_file), db, budget_tokens=700)
    assert tier == "truncated"
    assert len(content) <= 700 * 4 + 10  # within budget chars


@pytest.mark.unit
def test_load_tiered_handles_missing_file(db: sqlite3.Connection):
    """Missing file with no summary → empty string, truncated tier."""
    content, tier = load_tiered_content("/nonexistent/path.md", db, budget_tokens=100)
    assert tier == "truncated"
    assert content == ""
