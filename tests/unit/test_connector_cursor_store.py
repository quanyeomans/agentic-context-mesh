"""IM-1 unit tests for :class:`CursorStore`.

These tests exercise the real ``connector_cursors`` table via
``kairix.core.db.schema.create_schema`` so the round-trip is end-to-end
against SQLite, not against an in-memory fake. The store does NOT commit
on its own; the test (acting as the caller's per-batch transaction)
owns the commit. The atomicity test below proves that contract.

Per spec §4: cursor advance and chunk writes commit atomically in a
single per-batch SQLite transaction; on failure the cursor stays put
and the next worker tick retries the same range.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kairix.core.connectors.cursor_store import CursorStore
from kairix.core.db.schema import create_schema

pytestmark = pytest.mark.unit


def _open_db(tmp_path: Path) -> sqlite3.Connection:
    """Open a fresh SQLite connection against the kairix schema."""
    db = sqlite3.connect(str(tmp_path / "kairix.db"))
    create_schema(db)
    return db


def test_write_then_read_returns_token(tmp_path: Path) -> None:
    db = _open_db(tmp_path)
    try:
        store = CursorStore(db)
        store.write("agent-alpha", "cursor-token-1")
        db.commit()
        assert store.read("agent-alpha") == "cursor-token-1"
    finally:
        db.close()


def test_write_twice_updates_in_place(tmp_path: Path) -> None:
    db = _open_db(tmp_path)
    try:
        store = CursorStore(db)
        store.write("agent-alpha", "cursor-token-1")
        store.write("agent-alpha", "cursor-token-2")
        db.commit()
        assert store.read("agent-alpha") == "cursor-token-2"
        # Sanity: only one row exists for this source
        row_count = db.execute(
            "SELECT COUNT(*) FROM connector_cursors WHERE source_name = ?",
            ("agent-alpha",),
        ).fetchone()[0]
        assert row_count == 1
    finally:
        db.close()


def test_read_unknown_source_returns_none(tmp_path: Path) -> None:
    db = _open_db(tmp_path)
    try:
        store = CursorStore(db)
        assert store.read("does-not-exist") is None
    finally:
        db.close()


def test_atomicity_caller_owns_commit(tmp_path: Path) -> None:
    """Without an explicit commit, the write must not be visible to a fresh connection.

    This is the load-bearing test for the spec §4 "per-batch atomicity"
    invariant. If ``CursorStore.write`` commits internally, a fresh
    connection would observe the write — and a crash mid-batch would leak
    a half-applied state.
    """
    db_path = tmp_path / "kairix.db"
    writer_db = sqlite3.connect(str(db_path))
    create_schema(writer_db)
    try:
        store = CursorStore(writer_db)
        store.write("agent-alpha", "uncommitted-token")
        # Deliberately do NOT call writer_db.commit().
        reader_db = sqlite3.connect(str(db_path))
        try:
            visible = reader_db.execute(
                "SELECT cursor_token FROM connector_cursors WHERE source_name = ?",
                ("agent-alpha",),
            ).fetchone()
        finally:
            reader_db.close()
        # Sabotage-proof: if CursorStore.write calls self._db.commit() on its
        # own, ``visible`` becomes ("uncommitted-token",) and this assertion
        # fails. Verified by mutating write() to call self._db.commit() —
        # the assertion failed, restoring the impl returned to green.
        assert visible is None
    finally:
        writer_db.close()


def test_atomicity_holds_across_both_stores(tmp_path: Path) -> None:
    """Cursor + dead-letter writes share a single transaction.

    Lives here (not in the deadletter test file) because it asserts the
    cursor-side invariant: both stores share the same connection, neither
    commits internally, and a fresh connection sees nothing until the
    caller commits.
    """
    from kairix.core.connectors.dead_letter import DeadLetterStore

    db_path = tmp_path / "kairix.db"
    writer_db = sqlite3.connect(str(db_path))
    create_schema(writer_db)
    try:
        cursor_store = CursorStore(writer_db)
        deadletter_store = DeadLetterStore(writer_db)
        cursor_store.write("agent-alpha", "tok-A")
        deadletter_store.record("agent-alpha", "item-1", "boom")
        # No commit.
        reader_db = sqlite3.connect(str(db_path))
        try:
            cursor_row = reader_db.execute(
                "SELECT 1 FROM connector_cursors WHERE source_name = ?",
                ("agent-alpha",),
            ).fetchone()
            deadletter_row = reader_db.execute(
                "SELECT 1 FROM connector_deadletter WHERE source_name = ? AND item_id = ?",
                ("agent-alpha", "item-1"),
            ).fetchone()
        finally:
            reader_db.close()
        assert cursor_row is None
        assert deadletter_row is None
    finally:
        writer_db.close()
