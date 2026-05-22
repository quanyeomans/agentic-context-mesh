"""IM-1 unit tests for :class:`DeadLetterStore`.

Exercises the real ``connector_deadletter`` table via
``kairix.core.db.schema.create_schema``. The store does NOT commit on its
own — the per-batch transaction (the caller) owns the commit. Atomicity
of cursor + dead-letter writes is proven in
``tests/unit/test_connector_cursor_store.test_atomicity_holds_across_both_stores``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kairix.core.connectors.dead_letter import DeadLetterEntry, DeadLetterStore
from kairix.core.db.schema import create_schema

pytestmark = pytest.mark.unit


def _open_db(tmp_path: Path) -> sqlite3.Connection:
    """Open a fresh SQLite connection against the kairix schema."""
    db = sqlite3.connect(str(tmp_path / "kairix.db"))
    create_schema(db)
    return db


def test_first_record_returns_count_one(tmp_path: Path) -> None:
    db = _open_db(tmp_path)
    try:
        store = DeadLetterStore(db)
        count = store.record("agent-alpha", "item-1", "fetch failed")
        db.commit()
        assert count == 1
    finally:
        db.close()


def test_repeat_record_increments_count(tmp_path: Path) -> None:
    db = _open_db(tmp_path)
    try:
        store = DeadLetterStore(db)
        assert store.record("agent-alpha", "item-1", "boom") == 1
        assert store.record("agent-alpha", "item-1", "boom again") == 2
        assert store.record("agent-alpha", "item-1", "still boom") == 3
        db.commit()
        # is_poisoned uses default threshold 3.
        assert store.is_poisoned("agent-alpha", "item-1") is True
    finally:
        db.close()


def test_is_poisoned_false_below_threshold(tmp_path: Path) -> None:
    db = _open_db(tmp_path)
    try:
        store = DeadLetterStore(db)
        store.record("agent-alpha", "item-1", "boom")
        store.record("agent-alpha", "item-1", "boom again")
        db.commit()
        assert store.is_poisoned("agent-alpha", "item-1") is False
        # Custom threshold works.
        assert store.is_poisoned("agent-alpha", "item-1", threshold=2) is True
    finally:
        db.close()


def test_is_poisoned_unknown_item_returns_false(tmp_path: Path) -> None:
    db = _open_db(tmp_path)
    try:
        store = DeadLetterStore(db)
        assert store.is_poisoned("agent-alpha", "never-failed") is False
    finally:
        db.close()


def test_list_returns_dead_letter_entries(tmp_path: Path) -> None:
    db = _open_db(tmp_path)
    try:
        store = DeadLetterStore(db)
        store.record("agent-alpha", "item-1", "fetch failed")
        store.record("agent-alpha", "item-2", "extract failed")
        db.commit()
        entries = store.list()
        assert len(entries) == 2
        assert all(isinstance(entry, DeadLetterEntry) for entry in entries)
        item_ids = {entry.item_id for entry in entries}
        assert item_ids == {"item-1", "item-2"}
        for entry in entries:
            assert entry.source_name == "agent-alpha"
            assert entry.failure_count == 1
            assert entry.last_attempt  # ISO-8601 string populated
    finally:
        db.close()


def test_list_filters_by_source_name(tmp_path: Path) -> None:
    db = _open_db(tmp_path)
    try:
        store = DeadLetterStore(db)
        store.record("agent-alpha", "item-1", "boom")
        store.record("agent-beta", "item-2", "boom")
        db.commit()
        alpha_only = store.list(source_name="agent-alpha")
        beta_only = store.list(source_name="agent-beta")
        assert len(alpha_only) == 1
        assert alpha_only[0].source_name == "agent-alpha"
        assert alpha_only[0].item_id == "item-1"
        assert len(beta_only) == 1
        assert beta_only[0].source_name == "agent-beta"
        assert beta_only[0].item_id == "item-2"
    finally:
        db.close()


def test_cross_source_isolation_in_record(tmp_path: Path) -> None:
    """``record`` keys on ``(source_name, item_id)`` — same item_id, different source."""
    db = _open_db(tmp_path)
    try:
        store = DeadLetterStore(db)
        assert store.record("agent-alpha", "shared-id", "alpha boom") == 1
        assert store.record("agent-beta", "shared-id", "beta boom") == 1
        # Each source has its own count.
        assert store.record("agent-alpha", "shared-id", "alpha boom") == 2
        db.commit()
        assert store.is_poisoned("agent-alpha", "shared-id", threshold=2) is True
        assert store.is_poisoned("agent-beta", "shared-id", threshold=2) is False
    finally:
        db.close()


def test_record_does_not_commit(tmp_path: Path) -> None:
    """``record`` issues SQL but never commits — proven via fresh-connection visibility.

    Sabotage-proof: mutating ``DeadLetterStore.record`` to call
    ``self._db.commit()`` at the end made this assertion fail (the fresh
    reader saw the row). Restoring the impl returned to green.
    """
    db_path = tmp_path / "kairix.db"
    writer_db = sqlite3.connect(str(db_path))
    create_schema(writer_db)
    try:
        store = DeadLetterStore(writer_db)
        store.record("agent-alpha", "item-1", "boom")
        # No commit.
        reader_db = sqlite3.connect(str(db_path))
        try:
            visible = reader_db.execute(
                "SELECT 1 FROM connector_deadletter WHERE source_name = ? AND item_id = ?",
                ("agent-alpha", "item-1"),
            ).fetchone()
        finally:
            reader_db.close()
        assert visible is None
    finally:
        writer_db.close()
