"""Integration test for the ``bronze_ttl_gc`` feature flag (#316).

F54 both-branch coverage: OFF leaves bronze alone; ON deletes
bronze_records rows and raw blobs older than the TTL. Drives the real
:class:`FilesystemBronzeStore` through a scheduler-shaped closure that
reads the flag value off a :class:`FakeFeatureFlagResolver`.

F1 / F2 clean — no monkey-patching of kairix internals, no env-var
manipulation; the flag state is pinned through the canonical resolver
fake from ``tests/fakes.py``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kairix.core.connectors.bronze import FilesystemBronzeStore
from kairix.core.db.schema import create_schema
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.integration

_FLAG_NAME = "bronze_ttl_gc"
_BACKDATED_AT = "2020-01-01T00:00:00Z"


def _seed_backdated(store: FilesystemBronzeStore, db: sqlite3.Connection) -> str:
    """Write one row and backdate it past any reasonable TTL."""
    ref = store.write("agent-alpha", "backdated", b"backdated-bytes", "text/plain")
    db.execute(
        "UPDATE bronze_records SET fetched_at = ? WHERE source_name = 'agent-alpha'",
        (_BACKDATED_AT,),
    )
    db.commit()
    return ref.raw_path


def _run_ttl_stage(
    resolver: FakeFeatureFlagResolver,
    store: FilesystemBronzeStore,
    db: sqlite3.Connection,
    bronze_root: Path,
) -> int:
    """Mirror the production closure: flag-gated, per-source walk."""
    if not resolver.get(_FLAG_NAME):
        return 0
    total = 0
    for source_dir in bronze_root.iterdir():
        if source_dir.is_dir():
            total += store.gc_aged(source_dir.name, older_than_days=7)
    db.commit()
    return total


def test_flag_off_branch_leaves_bronze_intact(tmp_path: Path) -> None:
    bronze_root = tmp_path / "bronze"
    db = sqlite3.connect(str(tmp_path / "kairix.db"))
    try:
        create_schema(db)
        store = FilesystemBronzeStore(db, bronze_root)
        raw_path = _seed_backdated(store, db)

        resolver = FakeFeatureFlagResolver().with_flag("bronze_ttl_gc", False)
        deleted = _run_ttl_stage(resolver, store, db, bronze_root)

        assert deleted == 0
        assert (bronze_root / raw_path).is_file()
        rows = db.execute("SELECT count(*) FROM bronze_records WHERE source_name = 'agent-alpha'").fetchone()[0]
        assert rows == 1
    finally:
        db.close()


def test_flag_on_branch_deletes_backdated_rows_and_blobs(tmp_path: Path) -> None:
    bronze_root = tmp_path / "bronze"
    db = sqlite3.connect(str(tmp_path / "kairix.db"))
    try:
        create_schema(db)
        store = FilesystemBronzeStore(db, bronze_root)
        raw_path = _seed_backdated(store, db)

        resolver = FakeFeatureFlagResolver().with_flag("bronze_ttl_gc", True)
        deleted = _run_ttl_stage(resolver, store, db, bronze_root)

        assert deleted == 1
        assert not (bronze_root / raw_path).exists()
        rows = db.execute("SELECT count(*) FROM bronze_records WHERE source_name = 'agent-alpha'").fetchone()[0]
        assert rows == 0
    finally:
        db.close()


def test_flag_on_preserves_blobs_within_ttl(tmp_path: Path) -> None:
    """Sanity proof — even with flag ON, fresh blobs are protected by the TTL."""
    bronze_root = tmp_path / "bronze"
    db = sqlite3.connect(str(tmp_path / "kairix.db"))
    try:
        create_schema(db)
        store = FilesystemBronzeStore(db, bronze_root)
        ref = store.write("agent-alpha", "fresh", b"fresh", "text/plain")
        db.commit()

        resolver = FakeFeatureFlagResolver().with_flag("bronze_ttl_gc", True)
        deleted = _run_ttl_stage(resolver, store, db, bronze_root)
        assert deleted == 0
        assert (bronze_root / ref.raw_path).is_file()
    finally:
        db.close()
