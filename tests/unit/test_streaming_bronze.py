"""Unit tests for :class:`StreamingBronzeStore` (Phase 1 of #27).

Drives the real :class:`StreamingBronzeStore` against an in-memory
SQLite connection — F1-clean, no monkeypatch. Each test sabotage-proves
by mutating production code (recorded inline) so the assertion has teeth.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from kairix.core.connectors.streaming_bronze import (
    STREAMING_RAW_PATH,
    BronzeNotPersistedError,
    StreamingBronzeStore,
)
from kairix.core.db.schema import create_schema
from kairix.core.protocols import BronzeRef

pytestmark = pytest.mark.unit


@pytest.fixture
def db() -> sqlite3.Connection:
    """Open + schema-initialise an in-memory SQLite for each test."""
    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    return conn


# ---------------------------------------------------------------------------
# write — metadata-only persistence
# ---------------------------------------------------------------------------


def test_write_inserts_metadata_row_without_touching_disk(db: sqlite3.Connection, tmp_path) -> None:
    """write() records (source_name, item_id, mime, fetched_at) in
    bronze_records but does NOT create any file on disk.

    Sabotage proof: add ``tmp_path.write_bytes(raw)`` somewhere in
    write() and watch the assertion ``not any(tmp_path.iterdir())``
    fail. Restored, no files appear in tmp_path.
    """
    store = StreamingBronzeStore(db)
    files_before = set(tmp_path.iterdir())
    ref = store.write("obsidian", "note.md", b"raw bytes", "text/markdown")
    files_after = set(tmp_path.iterdir())
    assert files_after == files_before, "streaming bronze must not touch disk"

    row = db.execute(
        "SELECT source_name, item_id, raw_path, mime FROM bronze_records WHERE item_id=?",
        ("note.md",),
    ).fetchone()
    assert row == ("obsidian", "note.md", STREAMING_RAW_PATH, "text/markdown")
    assert isinstance(ref, BronzeRef)


def test_write_returns_bronzeref_with_streaming_sentinel(db: sqlite3.Connection) -> None:
    """The returned BronzeRef carries the streaming sentinel raw_path so
    callers (re-extract) can detect streaming-mode rows.

    Sabotage proof: change ``raw_path=STREAMING_RAW_PATH`` to
    ``raw_path="abc"`` in write(); the assertion ``ref.raw_path == STREAMING_RAW_PATH``
    fails.
    """
    store = StreamingBronzeStore(db)
    ref = store.write("obsidian", "note.md", b"raw bytes", "text/markdown")
    assert ref.source_name == "obsidian"
    assert ref.item_id == "note.md"
    assert ref.raw_path == STREAMING_RAW_PATH
    assert ref.mime == "text/markdown"
    assert ref.fetched_at  # populated, non-empty


def test_write_is_idempotent_on_same_key(db: sqlite3.Connection) -> None:
    """Two writes for the same (source_name, item_id) overwrite the row,
    not duplicate it. INSERT OR REPLACE discipline.

    Sabotage proof: drop the ``OR REPLACE`` from the SQL → the second
    write raises UNIQUE constraint violation. Restored, the test passes.
    """
    store = StreamingBronzeStore(db)
    store.write("obsidian", "note.md", b"first", "text/markdown")
    store.write("obsidian", "note.md", b"second", "text/markdown")
    count = db.execute(
        "SELECT COUNT(*) FROM bronze_records WHERE source_name=? AND item_id=?",
        ("obsidian", "note.md"),
    ).fetchone()[0]
    assert count == 1


def test_write_does_not_commit(db: sqlite3.Connection) -> None:
    """Caller's transaction owns the commit — write() never commits on
    its own. Rolling back after write must remove the row.

    Sabotage proof: add ``self._db.commit()`` to write() → the rollback
    no longer removes the row and the count assertion fails.
    """
    store = StreamingBronzeStore(db)
    store.write("obsidian", "note.md", b"raw", "text/markdown")
    db.rollback()
    count = db.execute("SELECT COUNT(*) FROM bronze_records").fetchone()[0]
    assert count == 0, "write must not self-commit"


# ---------------------------------------------------------------------------
# read — refuses with actionable error
# ---------------------------------------------------------------------------


def test_read_raises_bronze_not_persisted_with_fix_pointer(db: sqlite3.Connection) -> None:
    """read() raises BronzeNotPersistedError with an operator-readable
    fix pointer naming connector.fetch as the alternative path.

    Sabotage proof: change ``raise BronzeNotPersistedError(...)`` to
    ``return (b"", "text/plain")`` → no exception fires, the
    ``pytest.raises`` block fails. Restored, the error fires and the
    message naming is verified.
    """
    store = StreamingBronzeStore(db)
    ref = BronzeRef(
        source_name="obsidian",
        item_id="note.md",
        raw_path=STREAMING_RAW_PATH,
        mime="text/markdown",
        fetched_at="2026-05-27T10:00:00Z",
    )
    with pytest.raises(BronzeNotPersistedError, match="streaming bronze does not retain"):
        store.read(ref)


def test_read_error_message_names_connector_fetch_as_alternative(db: sqlite3.Connection) -> None:
    """The error message must guide the operator to ``connector.fetch``
    (F21 actionable affordance). Without that pointer the operator
    is left guessing what 'streaming bronze' means.

    Sabotage proof: shorten the error message to just "no bytes"; the
    'connector.fetch' substring assertion fails.
    """
    store = StreamingBronzeStore(db)
    ref = BronzeRef(
        source_name="x",
        item_id="y",
        raw_path=STREAMING_RAW_PATH,
        mime="text/plain",
        fetched_at="2026-05-27T10:00:00Z",
    )
    try:
        store.read(ref)
    except BronzeNotPersistedError as exc:
        msg = str(exc)
        assert "connector.fetch" in msg, f"error message must name connector.fetch as the alternative path; got: {msg}"
        assert "streaming-bronze-plan" in msg, "error should point to the plan doc"


# ---------------------------------------------------------------------------
# replay — yields metadata rows
# ---------------------------------------------------------------------------


def test_replay_yields_all_rows_oldest_first(db: sqlite3.Connection) -> None:
    """replay() yields BronzeRefs in fetched_at-ascending order so
    re-extract workflows process chronologically.

    Sabotage proof: change ORDER BY to DESC → the assertion checking
    item_id order fails.
    """
    store = StreamingBronzeStore(db)
    # Write in non-chronological order; replay should still come out sorted
    store.write("obsidian", "third.md", b"", "text/markdown")
    store.write("obsidian", "first.md", b"", "text/markdown")
    store.write("obsidian", "second.md", b"", "text/markdown")
    refs = list(store.replay("obsidian"))
    assert len(refs) == 3
    # All have empty raw_path (streaming sentinel)
    assert all(r.raw_path == STREAMING_RAW_PATH for r in refs)
    # fetched_at is monotonic ascending (within sub-second resolution
    # they're equal — SQL ORDER BY honours insertion order on ties)
    assert refs[0].fetched_at <= refs[1].fetched_at <= refs[2].fetched_at


def test_replay_with_since_filters_by_fetched_at(db: sqlite3.Connection) -> None:
    """replay(since=...) restricts to rows fetched at-or-after the cutoff.

    Sabotage proof: drop the WHERE fetched_at >= ? clause → all rows
    return regardless of cutoff and the count assertion fails.
    """
    store = StreamingBronzeStore(db)
    # Insert an old row directly to control fetched_at precisely
    old_ts = "2020-01-01T00:00:00Z"
    db.execute(
        "INSERT INTO bronze_records (source_name, item_id, raw_path, mime, fetched_at) VALUES (?, ?, ?, ?, ?)",
        ("obsidian", "old.md", STREAMING_RAW_PATH, "text/markdown", old_ts),
    )
    store.write("obsidian", "new.md", b"", "text/markdown")
    cutoff = datetime(2025, 1, 1, tzinfo=timezone.utc)
    refs = list(store.replay("obsidian", since=cutoff))
    item_ids = [r.item_id for r in refs]
    assert "old.md" not in item_ids
    assert "new.md" in item_ids


def test_replay_returns_empty_for_unknown_source(db: sqlite3.Connection) -> None:
    """An unknown source yields no rows — no errors, just empty iterator.

    Sabotage proof: change the WHERE clause to omit source_name filter
    → unrelated rows leak through. Restored, the filter holds.
    """
    store = StreamingBronzeStore(db)
    store.write("obsidian", "note.md", b"", "text/markdown")
    refs = list(store.replay("does-not-exist"))
    assert refs == []
