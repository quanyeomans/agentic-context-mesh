"""IM-2 unit tests for :class:`FilesystemBronzeStore`.

Drives the real ``bronze_records`` table + filesystem blob layout to
prove the per-method contract:

* ``write`` persists bytes at ``<bronze_root>/<source>/<hash[:2]>/<hash>``
  and writes a SQLite pointer row; no commit is issued internally.
* ``read`` round-trips the bytes back out.
* ``replay`` yields :class:`BronzeRef` rows in oldest-first order; the
  optional ``since`` filter restricts to rows at or after that
  timestamp.

The store accepts a :class:`sqlite3.Connection`; the test owns the
commit, exactly like the per-batch orchestrator does in production.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from kairix.core.connectors.bronze import FilesystemBronzeStore
from kairix.core.db.schema import create_schema

pytestmark = pytest.mark.unit


def _open_db(tmp_path: Path) -> sqlite3.Connection:
    """Open a fresh SQLite connection against the kairix schema."""
    db = sqlite3.connect(str(tmp_path / "kairix.db"))
    create_schema(db)
    return db


def test_write_persists_bytes_and_pointer_row(tmp_path: Path) -> None:
    db = _open_db(tmp_path)
    try:
        store = FilesystemBronzeStore(db, tmp_path / "bronze")
        ref = store.write("agent-alpha", "item-1", b"hello world", "text/plain")
        db.commit()

        # The blob lives at <bronze_root>/<source>/<hash[:2]>/<hash>.
        blob_path = tmp_path / "bronze" / ref.raw_path
        assert blob_path.is_file()
        assert blob_path.read_bytes() == b"hello world"

        # The pointer row exists.
        row = db.execute(
            "SELECT source_name, item_id, raw_path, mime FROM bronze_records WHERE source_name = ? AND item_id = ?",
            ("agent-alpha", "item-1"),
        ).fetchone()
        assert row is not None
        assert row[0] == "agent-alpha"
        assert row[1] == "item-1"
        assert row[2] == ref.raw_path
        assert row[3] == "text/plain"
    finally:
        db.close()


def test_write_is_idempotent_on_source_item_pair(tmp_path: Path) -> None:
    db = _open_db(tmp_path)
    try:
        store = FilesystemBronzeStore(db, tmp_path / "bronze")
        store.write("agent-alpha", "item-1", b"first", "text/plain")
        store.write("agent-alpha", "item-1", b"second", "text/plain")
        db.commit()

        row_count = db.execute(
            "SELECT COUNT(*) FROM bronze_records WHERE source_name = ? AND item_id = ?",
            ("agent-alpha", "item-1"),
        ).fetchone()[0]
        assert row_count == 1
    finally:
        db.close()


def test_atomicity_caller_owns_commit(tmp_path: Path) -> None:
    """Without an explicit commit, the bronze_records row is invisible to a fresh connection."""
    db_path = tmp_path / "kairix.db"
    db = sqlite3.connect(str(db_path))
    try:
        create_schema(db)
        store = FilesystemBronzeStore(db, tmp_path / "bronze")
        store.write("agent-alpha", "item-1", b"hello", "text/plain")
        # No db.commit() here — the test acts as the per-batch orchestrator
        # and rolls back instead.
        db.rollback()

        fresh = sqlite3.connect(str(db_path))
        try:
            row = fresh.execute("SELECT COUNT(*) FROM bronze_records").fetchone()
            assert row[0] == 0
        finally:
            fresh.close()
    finally:
        db.close()


def test_read_returns_bytes_and_mime(tmp_path: Path) -> None:
    db = _open_db(tmp_path)
    try:
        store = FilesystemBronzeStore(db, tmp_path / "bronze")
        ref = store.write("agent-alpha", "item-1", b"round-trip body", "text/markdown")
        db.commit()
        raw, mime = store.read(ref)
        assert raw == b"round-trip body"
        assert mime == "text/markdown"
    finally:
        db.close()


def test_read_raises_when_blob_missing(tmp_path: Path) -> None:
    db = _open_db(tmp_path)
    try:
        store = FilesystemBronzeStore(db, tmp_path / "bronze")
        ref = store.write("agent-alpha", "item-1", b"present", "text/plain")
        db.commit()
        # Remove the blob from disk; the pointer row still exists.
        (tmp_path / "bronze" / ref.raw_path).unlink()
        with pytest.raises(FileNotFoundError):
            store.read(ref)
    finally:
        db.close()


def test_replay_yields_refs_in_fetched_at_order(tmp_path: Path) -> None:
    db = _open_db(tmp_path)
    try:
        store = FilesystemBronzeStore(db, tmp_path / "bronze")
        # Three different items so distinct rows; the store sets
        # fetched_at to wall-clock time so the rows are monotonic.
        store.write("agent-alpha", "item-1", b"one", "text/plain")
        store.write("agent-alpha", "item-2", b"two", "text/plain")
        store.write("agent-alpha", "item-3", b"three", "text/plain")
        db.commit()

        refs = list(store.replay("agent-alpha"))
        assert [r.item_id for r in refs] == ["item-1", "item-2", "item-3"]
    finally:
        db.close()


def test_replay_filters_by_since(tmp_path: Path) -> None:
    db = _open_db(tmp_path)
    try:
        store = FilesystemBronzeStore(db, tmp_path / "bronze")
        store.write("agent-alpha", "item-1", b"one", "text/plain")
        store.write("agent-alpha", "item-2", b"two", "text/plain")
        db.commit()

        # Use a far-future cutoff so nothing matches.
        future = datetime(2099, 1, 1)
        refs = list(store.replay("agent-alpha", since=future))
        assert refs == []

        # Use a far-past cutoff so everything matches.
        past = datetime(1970, 1, 1)
        refs = list(store.replay("agent-alpha", since=past))
        assert {r.item_id for r in refs} == {"item-1", "item-2"}
    finally:
        db.close()


def test_replay_scopes_to_source_name(tmp_path: Path) -> None:
    db = _open_db(tmp_path)
    try:
        store = FilesystemBronzeStore(db, tmp_path / "bronze")
        store.write("agent-alpha", "item-1", b"alpha", "text/plain")
        store.write("agent-beta", "item-2", b"beta", "text/plain")
        db.commit()

        alpha_refs = list(store.replay("agent-alpha"))
        assert [r.item_id for r in alpha_refs] == ["item-1"]
        beta_refs = list(store.replay("agent-beta"))
        assert [r.item_id for r in beta_refs] == ["item-2"]
    finally:
        db.close()
