"""
Staleness tracking for Mnemosyne summaries.

Maintains summaries.db — a sqlite database that records L0/L1 summaries
alongside the source file mtime at generation time. A summary is stale
when the source file mtime is newer than when the summary was generated.
"""

import sqlite3
from pathlib import Path

from mnemosyne.summaries.generate import SummaryResult

SUMMARIES_DB = Path("/data/mnemosyne/summaries.db")

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS summaries (
    path            TEXT PRIMARY KEY,
    l0              TEXT,
    l1              TEXT,
    model           TEXT,
    generated_at    TEXT,
    source_mtime    REAL
);
"""


def init_summaries_db(db: sqlite3.Connection) -> None:
    """Create summaries table if it does not already exist."""
    db.execute(_CREATE_TABLE)
    db.commit()


# ---------------------------------------------------------------------------
# Staleness checks
# ---------------------------------------------------------------------------


def is_stale(path: str, db: sqlite3.Connection) -> bool:
    """
    Return True if the file has no summary or the source mtime is newer
    than the stored source_mtime (i.e. file changed since last summary).
    """
    row = db.execute("SELECT source_mtime FROM summaries WHERE path = ?", (path,)).fetchone()

    if row is None:
        return True  # No summary at all

    stored_mtime: float = row[0]
    try:
        current_mtime = Path(path).stat().st_mtime
    except FileNotFoundError:
        return True  # File gone — treat as stale

    return current_mtime > stored_mtime


def write_summary(result: SummaryResult, db: sqlite3.Connection) -> None:
    """Upsert (insert or replace) a summary record."""
    try:
        source_mtime = Path(result.path).stat().st_mtime
    except FileNotFoundError:
        source_mtime = 0.0

    db.execute(
        """
        INSERT INTO summaries (path, l0, l1, model, generated_at, source_mtime)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            l0 = excluded.l0,
            l1 = excluded.l1,
            model = excluded.model,
            generated_at = excluded.generated_at,
            source_mtime = excluded.source_mtime
        """,
        (result.path, result.l0, result.l1, result.model, result.generated_at, source_mtime),
    )
    db.commit()


def get_stale_paths(all_paths: list[str], db: sqlite3.Connection) -> list[str]:
    """Return the subset of paths that are missing or have stale summaries."""
    return [p for p in all_paths if is_stale(p, db)]


def get_summary(path: str, db: sqlite3.Connection) -> SummaryResult | None:
    """Retrieve the stored summary for a path, or None if not found."""
    row = db.execute(
        "SELECT path, l0, l1, model, generated_at FROM summaries WHERE path = ?",
        (path,),
    ).fetchone()

    if row is None:
        return None

    return SummaryResult(
        path=row[0],
        l0=row[1] or "",
        l1=row[2],
        model=row[3] or "",
        generated_at=row[4] or "",
        tokens_used=0,  # Not stored in DB; set to 0 on retrieval
    )
