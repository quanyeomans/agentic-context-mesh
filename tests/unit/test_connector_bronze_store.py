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


def test_reap_orphans_deletes_unreferenced_blobs(tmp_path: Path) -> None:
    """Orphan = on-disk file under <bronze_root>/<source>/ with no row in
    bronze_records pointing at it. The post-fsync-pre-commit crash window
    (module docstring §"Atomicity") produces these; the reaper closes it.
    """
    db = _open_db(tmp_path)
    try:
        store = FilesystemBronzeStore(db, tmp_path / "bronze")
        ref = store.write("agent-alpha", "tracked", b"tracked-bytes", "text/plain")
        db.commit()

        orphan_hash = "f" * 64
        orphan_dir = tmp_path / "bronze" / "agent-alpha" / orphan_hash[:2]
        orphan_dir.mkdir(parents=True, exist_ok=True)
        orphan_path = orphan_dir / orphan_hash
        orphan_path.write_bytes(b"unreferenced")

        reaped = store.reap_orphans("agent-alpha")
        assert reaped == 1
        assert not orphan_path.exists()
        # Tracked blob survives — the registry row points to it.
        assert (tmp_path / "bronze" / ref.raw_path).is_file()
    finally:
        db.close()


def test_reap_orphans_returns_zero_on_clean_store(tmp_path: Path) -> None:
    db = _open_db(tmp_path)
    try:
        store = FilesystemBronzeStore(db, tmp_path / "bronze")
        store.write("agent-alpha", "tracked", b"tracked-bytes", "text/plain")
        db.commit()
        assert store.reap_orphans("agent-alpha") == 0
    finally:
        db.close()


def test_reap_orphans_returns_zero_when_source_dir_absent(tmp_path: Path) -> None:
    db = _open_db(tmp_path)
    try:
        store = FilesystemBronzeStore(db, tmp_path / "bronze")
        assert store.reap_orphans("never-fetched-source") == 0
    finally:
        db.close()


def test_reap_orphans_min_age_protects_in_flight_writes(tmp_path: Path) -> None:
    """min_age_seconds protects blobs newer than the cutoff so an
    in-flight write isn't reaped mid-fsync."""
    db = _open_db(tmp_path)
    try:
        store = FilesystemBronzeStore(db, tmp_path / "bronze")
        # Create a fresh orphan; mtime is "now".
        orphan_hash = "a" * 64
        orphan_dir = tmp_path / "bronze" / "agent-alpha" / orphan_hash[:2]
        orphan_dir.mkdir(parents=True, exist_ok=True)
        orphan_path = orphan_dir / orphan_hash
        orphan_path.write_bytes(b"recent")

        # A huge min_age cutoff leaves the recent orphan in place.
        reaped = store.reap_orphans("agent-alpha", min_age_seconds=3600.0)
        assert reaped == 0
        assert orphan_path.exists()

        # No cutoff reaps it.
        reaped = store.reap_orphans("agent-alpha")
        assert reaped == 1
        assert not orphan_path.exists()
    finally:
        db.close()


def test_reap_orphans_scopes_to_named_source(tmp_path: Path) -> None:
    """Orphans under one source must not affect blobs under another."""
    db = _open_db(tmp_path)
    try:
        store = FilesystemBronzeStore(db, tmp_path / "bronze")
        ref_beta = store.write("agent-beta", "beta-tracked", b"beta-bytes", "text/plain")
        db.commit()

        # Create an orphan under agent-alpha only.
        orphan_hash = "b" * 64
        orphan_dir = tmp_path / "bronze" / "agent-alpha" / orphan_hash[:2]
        orphan_dir.mkdir(parents=True, exist_ok=True)
        (orphan_dir / orphan_hash).write_bytes(b"alpha-orphan")

        # Reaping agent-beta finds no orphans there.
        assert store.reap_orphans("agent-beta") == 0
        assert (tmp_path / "bronze" / ref_beta.raw_path).is_file()

        # Reaping agent-alpha reaps the orphan.
        assert store.reap_orphans("agent-alpha") == 1
    finally:
        db.close()


def test_gc_aged_deletes_blobs_older_than_ttl(tmp_path: Path) -> None:
    """#316 — TTL GC drops blobs whose fetched_at is older than the cutoff."""
    db = _open_db(tmp_path)
    try:
        store = FilesystemBronzeStore(db, tmp_path / "bronze")
        ref = store.write("agent-alpha", "old-item", b"old-bytes", "text/plain")
        db.commit()

        # Backdate the fetched_at well past any reasonable TTL.
        db.execute(
            "UPDATE bronze_records SET fetched_at = '2020-01-01T00:00:00Z' "
            "WHERE source_name = 'agent-alpha' AND item_id = 'old-item'"
        )
        db.commit()

        deleted = store.gc_aged("agent-alpha", older_than_days=7)
        db.commit()

        assert deleted == 1
        # Blob is gone; bronze_records row is gone.
        assert not (tmp_path / "bronze" / ref.raw_path).exists()
        rows = db.execute("SELECT count(*) FROM bronze_records WHERE source_name = 'agent-alpha'").fetchone()[0]
        assert rows == 0
    finally:
        db.close()


def test_gc_aged_preserves_blobs_within_ttl(tmp_path: Path) -> None:
    """A blob written 'now' (within the TTL window) must survive."""
    db = _open_db(tmp_path)
    try:
        store = FilesystemBronzeStore(db, tmp_path / "bronze")
        ref = store.write("agent-alpha", "fresh-item", b"fresh", "text/plain")
        db.commit()

        deleted = store.gc_aged("agent-alpha", older_than_days=7)
        assert deleted == 0
        assert (tmp_path / "bronze" / ref.raw_path).is_file()
    finally:
        db.close()


def test_gc_aged_zero_ttl_deletes_everything(tmp_path: Path) -> None:
    """older_than_days=0 means cutoff = now; every row qualifies."""
    db = _open_db(tmp_path)
    try:
        store = FilesystemBronzeStore(db, tmp_path / "bronze")
        store.write("agent-alpha", "item-1", b"one", "text/plain")
        store.write("agent-alpha", "item-2", b"two", "text/plain")
        db.commit()

        # Backdate both rows so even a 0-day TTL catches them.
        db.execute("UPDATE bronze_records SET fetched_at = '2020-01-01T00:00:00Z' WHERE source_name = 'agent-alpha'")
        db.commit()

        deleted = store.gc_aged("agent-alpha", older_than_days=0)
        db.commit()
        assert deleted == 2
    finally:
        db.close()


def test_gc_aged_refuses_negative_ttl(tmp_path: Path) -> None:
    """Negative TTL is operator error; refuse with an actionable message."""
    db = _open_db(tmp_path)
    try:
        store = FilesystemBronzeStore(db, tmp_path / "bronze")
        with pytest.raises(ValueError, match="older_than_days must be >= 0"):
            store.gc_aged("agent-alpha", older_than_days=-1)
    finally:
        db.close()


def test_gc_aged_scopes_to_named_source(tmp_path: Path) -> None:
    """GC for one source must not touch blobs in another."""
    db = _open_db(tmp_path)
    try:
        store = FilesystemBronzeStore(db, tmp_path / "bronze")
        ref_beta = store.write("agent-beta", "beta-item", b"beta", "text/plain")
        store.write("agent-alpha", "alpha-item", b"alpha", "text/plain")
        db.commit()

        # Backdate everything; cull only agent-alpha.
        db.execute("UPDATE bronze_records SET fetched_at = '2020-01-01T00:00:00Z'")
        db.commit()

        deleted = store.gc_aged("agent-alpha", older_than_days=0)
        db.commit()
        assert deleted == 1

        # agent-beta survives.
        assert (tmp_path / "bronze" / ref_beta.raw_path).is_file()
        assert db.execute("SELECT count(*) FROM bronze_records WHERE source_name = 'agent-beta'").fetchone()[0] == 1
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
