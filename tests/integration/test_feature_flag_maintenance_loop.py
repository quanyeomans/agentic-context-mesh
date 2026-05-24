"""F54 integration coverage for the ``maintenance_loop`` feature flag.

Per F54 (docs/architecture/feature-flag-architecture.md §5): every flag
needs an integration test exercising both branches via
``FakeFeatureFlagResolver`` from ``tests/fakes.py``.

This file pins the worker-loop dispatch contract: when the flag is OFF
the maintenance tick never fires (no DB open, no scheduler instantiated);
when ON the production
:func:`kairix.worker.run_maintenance_loop_tick` returns a populated
:class:`MaintenanceTickResult` envelope.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kairix.core.db.schema import create_schema
from kairix.core.maintenance import MaintenanceTickResult
from kairix.worker import MaintenanceLoopDeps, run_maintenance_loop_tick
from tests.fakes import FakeFeatureFlagResolver


def _bootstrap(tmp_path: Path, *, seed_orphan: bool = False) -> Path:
    """Create the schema; optionally seed one orphan content_vectors row.

    Returns the SQLite file path so callers can build a Deps that opens
    a fresh connection per call (production semantics).
    """
    db_path = tmp_path / "kairix.sqlite"
    db = sqlite3.connect(str(db_path))
    try:
        create_schema(db, dims=4)
        if seed_orphan:
            db.execute(
                "INSERT INTO content_vectors (hash, seq, pos) VALUES (?, ?, ?)",
                ("orphan-hash-flag-int", 0, 0),
            )
            db.commit()
    finally:
        db.close()
    return db_path


@pytest.mark.integration
def test_flag_off_no_tick_fires(tmp_path: Path) -> None:
    """OFF branch: the wrapper returns None and no DB writes happen.

    Sabotage proof: rewrite ``run_maintenance_loop_tick`` to ignore the
    flag (``if not deps.flag_reader("maintenance_loop"):`` →
    ``if False:``) and the assertion that ``result is None`` fails.
    """
    resolver = FakeFeatureFlagResolver().with_flag("maintenance_loop", False)
    db_path = _bootstrap(tmp_path, seed_orphan=True)

    deps = MaintenanceLoopDeps(
        flag_reader=resolver.get,
        db_factory=lambda: sqlite3.connect(str(db_path)),
        retention_days_resolver=lambda: 7,
    )
    result = run_maintenance_loop_tick(deps)

    assert result is None, f"OFF branch should return None; got {result!r}"

    # Orphan untouched, pruned table empty.
    db = sqlite3.connect(str(db_path))
    try:
        orphan_count = db.execute(
            "SELECT COUNT(*) FROM content_vectors WHERE hash = 'orphan-hash-flag-int'"
        ).fetchone()[0]
        pruned_count = db.execute("SELECT COUNT(*) FROM content_vectors_pruned").fetchone()[0]
    finally:
        db.close()
    assert orphan_count == 1, "OFF branch must not delete content_vectors"
    assert pruned_count == 0, "OFF branch must not write content_vectors_pruned"


@pytest.mark.integration
def test_flag_on_tick_fires_and_prunes_orphan(tmp_path: Path) -> None:
    """ON branch: the production wrapper runs a real tick + prunes.

    Sabotage proof: change the orphan-detection SQL to ``WHERE 1=0`` and
    the ``orphans_pruned >= 1`` assertion fails because no row is moved.
    """
    resolver = FakeFeatureFlagResolver().with_flag("maintenance_loop", True)
    db_path = _bootstrap(tmp_path, seed_orphan=True)

    deps = MaintenanceLoopDeps(
        flag_reader=resolver.get,
        db_factory=lambda: sqlite3.connect(str(db_path)),
        retention_days_resolver=lambda: 7,
    )
    result = run_maintenance_loop_tick(deps)

    assert isinstance(result, MaintenanceTickResult), f"expected MaintenanceTickResult; got {result!r}"
    assert result.orphans_pruned >= 1, f"expected ≥1 orphan pruned; got {result.orphans_pruned}"

    # Orphan moved into the soft-delete table.
    db = sqlite3.connect(str(db_path))
    try:
        pruned_count = db.execute(
            "SELECT COUNT(*) FROM content_vectors_pruned WHERE hash = 'orphan-hash-flag-int'"
        ).fetchone()[0]
        live_count = db.execute("SELECT COUNT(*) FROM content_vectors WHERE hash = 'orphan-hash-flag-int'").fetchone()[
            0
        ]
    finally:
        db.close()
    assert pruned_count == 1, f"expected orphan in soft-delete table; got {pruned_count}"
    assert live_count == 0, f"expected orphan removed from live table; got {live_count}"


@pytest.mark.integration
def test_flag_on_second_tick_is_idempotent(tmp_path: Path) -> None:
    """Idempotency: a second tick on a clean DB reports orphans_pruned=0.

    Sabotage proof: drop the ``INSERT OR IGNORE`` guard in
    ``MaintenanceScheduler._prune_orphans`` (change to plain ``INSERT``)
    and the second tick raises a UNIQUE-constraint error before this
    assertion runs.
    """
    resolver = FakeFeatureFlagResolver().with_flag("maintenance_loop", True)
    db_path = _bootstrap(tmp_path, seed_orphan=True)

    deps = MaintenanceLoopDeps(
        flag_reader=resolver.get,
        db_factory=lambda: sqlite3.connect(str(db_path)),
        retention_days_resolver=lambda: 7,
    )
    first = run_maintenance_loop_tick(deps)
    second = run_maintenance_loop_tick(deps)

    assert first is not None and second is not None
    assert first.orphans_pruned == 1
    assert second.orphans_pruned == 0, (
        f"second tick on clean DB must be a no-op; got orphans_pruned={second.orphans_pruned}"
    )


@pytest.mark.integration
def test_flag_state_reflects_resolver() -> None:
    """The flag's effective value matches what the resolver reports."""
    on = FakeFeatureFlagResolver().with_flag("maintenance_loop", True)
    off = FakeFeatureFlagResolver().with_flag("maintenance_loop", False)
    assert on.get("maintenance_loop") is True
    assert off.get("maintenance_loop") is False
