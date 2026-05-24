"""E2E composed path for KFEAT-021 Phase 1 — maintenance_loop flag.

Per F48 sibling-test pattern (the brief calls for an E2E composed-path
test even though the related_spec lives outside the four
F54-canonical top-level spec roots).

This file exercises the composed production path end-to-end:

1. **Bootstrap** a tmp SQLite DB through ``create_schema`` — the real
   v3 schema including the KFEAT-021 ``content_vectors_pruned`` table.
2. **Seed** five orphan ``content_vectors`` rows (different hashes /
   seqs to exercise the multi-orphan prune loop).
3. **Compose** the worker-loop dispatch through
   :func:`kairix.worker.run_maintenance_loop_tick` with the
   ``maintenance_loop`` flag pinned ON via
   :class:`FakeFeatureFlagResolver`. This is the SAME function the
   production ``main()`` loop calls — no scaffolding bypass.
4. **Assert** the structured outcome: orphans pruned, soft-delete
   table populated, completion log event emitted.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest

from kairix.core.db.schema import create_schema
from kairix.core.maintenance import (
    EVENT_TICK_COMPLETED,
    EVENT_TICK_STARTED,
    MaintenanceTickResult,
)
from kairix.worker import MaintenanceLoopDeps, run_maintenance_loop_tick
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.e2e


def _bootstrap_e2e_db(tmp_path: Path) -> Path:
    """Bring the production schema up; seed 5 distinct orphan rows."""
    db_path = tmp_path / "kairix.sqlite"
    db = sqlite3.connect(str(db_path))
    try:
        create_schema(db, dims=4)
        # Five orphan content_vectors rows — different hashes so the
        # prune loop walks five iterations. No matching documents row
        # for any of them, which is exactly the leak shape KFEAT-021
        # Phase 1 cleans up.
        for i in range(5):
            db.execute(
                "INSERT INTO content_vectors (hash, seq, pos) VALUES (?, ?, ?)",
                (f"orphan-{i}", 0, 0),
            )
        db.commit()
    finally:
        db.close()
    return db_path


def test_composed_maintenance_loop_path_prunes_seeded_orphans(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """End-to-end: schema → seed orphans → composed tick → prune + log assertions.

    Per F48: the test exercises every layer of the maintenance composed
    path against the real ``content_vectors`` / ``content_vectors_pruned``
    schema rows, through the real ``MaintenanceScheduler`` constructed
    by the production ``run_maintenance_loop_tick`` helper.
    """
    db_path = _bootstrap_e2e_db(tmp_path)
    resolver = FakeFeatureFlagResolver().with_flag("maintenance_loop", True)

    deps = MaintenanceLoopDeps(
        flag_reader=resolver.get,
        db_factory=lambda: sqlite3.connect(str(db_path)),
        retention_days_resolver=lambda: 7,
    )

    with caplog.at_level(logging.INFO, logger="kairix.maintenance"):
        result = run_maintenance_loop_tick(deps)

    # ---- Result envelope --------------------------------------------------
    assert isinstance(result, MaintenanceTickResult), f"expected MaintenanceTickResult; got {result!r}"
    assert result.orphans_pruned == 5, f"expected exactly 5 seeded orphans pruned; got {result.orphans_pruned}"
    assert result.pruned_table_size == 5, (
        f"expected 5 rows in content_vectors_pruned after tick; got {result.pruned_table_size}"
    )
    assert result.current_orphan_count == 0, (
        f"expected 0 surviving orphans after tick; got {result.current_orphan_count}"
    )
    assert result.elapsed_ms >= 0, "elapsed_ms must be non-negative"

    # ---- DB-level proofs --------------------------------------------------
    db = sqlite3.connect(str(db_path))
    try:
        live_orphans = db.execute("SELECT COUNT(*) FROM content_vectors WHERE hash LIKE 'orphan-%'").fetchone()[0]
        soft_deleted = db.execute("SELECT COUNT(*) FROM content_vectors_pruned WHERE hash LIKE 'orphan-%'").fetchone()[
            0
        ]
        pruned_at_rows = db.execute(
            "SELECT pruned_at FROM content_vectors_pruned WHERE hash LIKE 'orphan-%' ORDER BY hash"
        ).fetchall()
    finally:
        db.close()

    assert live_orphans == 0, f"expected all live orphans cleared; got {live_orphans}"
    assert soft_deleted == 5, f"expected 5 soft-deleted orphans; got {soft_deleted}"
    # Every soft-deleted row carries an ISO-8601-Z timestamp.
    for (pruned_at,) in pruned_at_rows:
        assert pruned_at.endswith("Z"), f"expected ISO-Z timestamp; got {pruned_at!r}"

    # ---- Structured log proofs -------------------------------------------
    messages = [rec.getMessage() for rec in caplog.records]
    started_lines = [m for m in messages if EVENT_TICK_STARTED in m]
    completed_lines = [m for m in messages if EVENT_TICK_COMPLETED in m]
    assert started_lines, f"expected at least one event={EVENT_TICK_STARTED} log line; got: {messages!r}"
    assert completed_lines, f"expected at least one event={EVENT_TICK_COMPLETED} log line; got: {messages!r}"
    # The completion line carries the orphan count for log-only consumers.
    assert any("orphans_pruned=5" in m for m in completed_lines), (
        f"expected orphans_pruned=5 in completion log; got: {completed_lines!r}"
    )


def test_composed_maintenance_loop_path_flag_off_is_noop(tmp_path: Path) -> None:
    """OFF branch composed-path: no tick fires, no soft-delete writes.

    Sabotage proof: drop the flag check in ``run_maintenance_loop_tick``
    and the orphan rows get pruned (the seeded rows would disappear
    from ``content_vectors``).
    """
    db_path = _bootstrap_e2e_db(tmp_path)
    resolver = FakeFeatureFlagResolver().with_flag("maintenance_loop", False)

    deps = MaintenanceLoopDeps(
        flag_reader=resolver.get,
        db_factory=lambda: sqlite3.connect(str(db_path)),
        retention_days_resolver=lambda: 7,
    )

    result = run_maintenance_loop_tick(deps)

    assert result is None, f"OFF branch must short-circuit to None; got {result!r}"

    db = sqlite3.connect(str(db_path))
    try:
        live = db.execute("SELECT COUNT(*) FROM content_vectors WHERE hash LIKE 'orphan-%'").fetchone()[0]
        pruned = db.execute("SELECT COUNT(*) FROM content_vectors_pruned").fetchone()[0]
    finally:
        db.close()
    assert live == 5, f"OFF branch must not delete content_vectors; got {live}"
    assert pruned == 0, f"OFF branch must not write content_vectors_pruned; got {pruned}"
