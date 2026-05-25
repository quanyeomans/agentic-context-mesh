"""E2E composed path for #316 — bronze_ttl_gc flag.

Per F48 + F54 sibling-test pattern: ``bronze_ttl_gc``'s related_spec
references ``docs/architecture/connector-ingestion-architecture.md``
which is a top-level capability spec, so F54 mandates an E2E
composed-path test alongside the BDD + integration coverage.

This file exercises the composed production path end-to-end:

1. **Bootstrap** a tmp SQLite DB through ``create_schema`` plus a real
   :class:`FilesystemBronzeStore` rooted under ``tmp_path``.
2. **Seed** two backdated bronze records (fetched_at far in the past)
   plus one fresh record (fetched_at now) so the TTL boundary actually
   discriminates.
3. **Compose** the scheduler-shaped TTL closure with the flag pinned
   via :class:`FakeFeatureFlagResolver` — the SAME flag-gated shape
   the production default closure uses.
4. **Assert**: backdated rows + blobs are gone; fresh row + blob
   survive. Sabotage proof — flip the flag OFF and nothing is deleted.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kairix.core.connectors.bronze import FilesystemBronzeStore
from kairix.core.db.schema import create_schema
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.e2e

_BACKDATED_AT = "2020-01-01T00:00:00Z"


def _bootstrap(tmp_path: Path) -> tuple[Path, sqlite3.Connection, FilesystemBronzeStore]:
    bronze_root = tmp_path / "bronze"
    db = sqlite3.connect(str(tmp_path / "kairix.db"))
    create_schema(db)
    store = FilesystemBronzeStore(db, bronze_root)
    return bronze_root, db, store


def _seed_backdated_and_fresh(store: FilesystemBronzeStore, db: sqlite3.Connection) -> tuple[list[str], str]:
    """Two backdated rows + one fresh row for the same source."""
    backdated_paths: list[str] = []
    for item_id in ("old-1", "old-2"):
        ref = store.write("agent-alpha", item_id, item_id.encode(), "text/plain")
        backdated_paths.append(ref.raw_path)
    fresh_ref = store.write("agent-alpha", "fresh-1", b"fresh-bytes", "text/plain")
    db.execute(
        "UPDATE bronze_records SET fetched_at = ? WHERE source_name = 'agent-alpha' AND item_id IN ('old-1', 'old-2')",
        (_BACKDATED_AT,),
    )
    db.commit()
    return backdated_paths, fresh_ref.raw_path


def _run_ttl_stage(
    resolver: FakeFeatureFlagResolver,
    store: FilesystemBronzeStore,
    db: sqlite3.Connection,
    bronze_root: Path,
) -> int:
    """Mirror the production default closure: flag-gated, per-source walk."""
    if not resolver.get("bronze_ttl_gc"):
        return 0
    total = 0
    for source_dir in bronze_root.iterdir():
        if source_dir.is_dir():
            total += store.gc_aged(source_dir.name, older_than_days=7)
    db.commit()
    return total


def test_composed_bronze_ttl_gc_path_flag_on_drops_aged_blobs(tmp_path: Path) -> None:
    """ON branch end-to-end: backdated rows + blobs gone, fresh row survives."""
    bronze_root, db, store = _bootstrap(tmp_path)
    try:
        backdated_paths, fresh_path = _seed_backdated_and_fresh(store, db)

        resolver = FakeFeatureFlagResolver().with_flag("bronze_ttl_gc", True)
        deleted = _run_ttl_stage(resolver, store, db, bronze_root)

        assert deleted == 2
        for p in backdated_paths:
            assert not (bronze_root / p).exists()
        # Fresh record + blob untouched.
        assert (bronze_root / fresh_path).is_file()
        rows = db.execute("SELECT count(*) FROM bronze_records WHERE source_name = 'agent-alpha'").fetchone()[0]
        assert rows == 1
    finally:
        db.close()


def test_composed_bronze_ttl_gc_path_flag_off_leaves_bronze_intact(tmp_path: Path) -> None:
    """OFF branch end-to-end: nothing is deleted even though TTL has elapsed."""
    bronze_root, db, store = _bootstrap(tmp_path)
    try:
        backdated_paths, fresh_path = _seed_backdated_and_fresh(store, db)

        resolver = FakeFeatureFlagResolver().with_flag("bronze_ttl_gc", False)
        deleted = _run_ttl_stage(resolver, store, db, bronze_root)

        assert deleted == 0
        # All three bronze records still present.
        for p in [*backdated_paths, fresh_path]:
            assert (bronze_root / p).is_file()
        rows = db.execute("SELECT count(*) FROM bronze_records WHERE source_name = 'agent-alpha'").fetchone()[0]
        assert rows == 3
    finally:
        db.close()
