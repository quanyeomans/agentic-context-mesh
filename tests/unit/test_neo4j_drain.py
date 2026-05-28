"""Unit tests for the Neo4j drain (GH #334).

Covers the small drain branches the integration suite alone doesn't
reach. Every test drives the public surface
(:class:`Neo4jDrainer` constructor, :func:`run_neo4j_drain_tick`,
:func:`kairix.core.factory.build_neo4j_drainer`) — no imports of
underscored internals (F5 / no-tests-against-private-functions).

F1-clean (no patching), F2-clean (no env vars).
"""

from __future__ import annotations

import sqlite3

import pytest

from kairix.core import factory
from kairix.core.curator.drain import (
    DEFAULT_DRAIN_BATCH_SIZE,
    Neo4jDrainer,
    run_neo4j_drain_tick,
)
from kairix.core.db.schema import create_schema
from tests.fakes import FakeDrainGraphRepository

pytestmark = pytest.mark.unit


def _open() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    create_schema(db)
    return db


def _insert(db: sqlite3.Connection, *, kind: str, value: str) -> int:
    cur = db.execute(
        "INSERT INTO entity_signals (kind, value, source_uri, modified_at, confidence, "
        "sensitivity, pushed_to_neo4j, push_attempt_count) "
        "VALUES (?, ?, 'vault://x', '2026-05-25T10:00:00Z', 0.9, 'internal', 0, 0)",
        (kind, value),
    )
    db.commit()
    return int(cur.lastrowid or 0)


def test_neo4j_drainer_rejects_zero_batch_size() -> None:
    """Constructor raises ValueError when ``batch_size <= 0`` per the affordance.

    Drives the public Neo4jDrainer constructor — no private internals.
    """
    db = _open()
    try:
        with pytest.raises(ValueError, match="batch_size must be > 0"):
            Neo4jDrainer(db, FakeDrainGraphRepository(), batch_size=0)
        with pytest.raises(ValueError, match="batch_size must be > 0"):
            Neo4jDrainer(db, FakeDrainGraphRepository(), batch_size=-1)
    finally:
        db.close()


def test_neo4j_drainer_batch_size_property_exposes_constructor_value() -> None:
    """The ``batch_size`` property is the operator-facing view of the configured cap."""
    db = _open()
    try:
        drainer = Neo4jDrainer(db, FakeDrainGraphRepository(), batch_size=42)
        assert drainer.batch_size == 42
    finally:
        db.close()


def test_neo4j_drainer_declares_per_tick_class_attributes() -> None:
    """F66 — the drainer class declares per_tick_max_items + watermark attrs.

    Tests the public class shape — no private symbol imports.
    """
    assert Neo4jDrainer.per_tick_max_items == DEFAULT_DRAIN_BATCH_SIZE
    # Watermark exempt with rationale comment above the class — value is None.
    assert Neo4jDrainer.disk_watermark_min_free_bytes is None


def test_factory_build_neo4j_drainer_returns_neo4j_drainer_instance() -> None:
    """The F47 factory entry point returns a Neo4jDrainer with the configured batch_size."""
    db = _open()
    try:
        drainer = factory.build_neo4j_drainer(db=db, repo=FakeDrainGraphRepository(), batch_size=7)
        assert isinstance(drainer, Neo4jDrainer)
        assert drainer.batch_size == 7
    finally:
        db.close()


def test_factory_build_neo4j_drainer_uses_default_batch_size_when_not_supplied() -> None:
    """Factory default batch_size falls through to DEFAULT_DRAIN_BATCH_SIZE."""
    db = _open()
    try:
        drainer = factory.build_neo4j_drainer(db=db, repo=FakeDrainGraphRepository())
        assert drainer.batch_size == DEFAULT_DRAIN_BATCH_SIZE
    finally:
        db.close()


def test_run_neo4j_drain_tick_marks_failed_row_and_continues_to_next() -> None:
    """Per-row failure marks the row -1 + last_push_error, then continues.

    Drives the public ``run_neo4j_drain_tick`` function.
    """
    db = _open()
    try:
        sid_ok = _insert(db, kind="person", value="ok-person")
        sid_bad = _insert(db, kind="person", value="bad-person")
        repo = FakeDrainGraphRepository(available=True, raise_on_value="bad-person")
        result = run_neo4j_drain_tick(db, repo)
        assert result.pushed == 1
        assert result.failed == 1
        # Verify the failed row carries the error text.
        err = db.execute("SELECT last_push_error FROM entity_signals WHERE id = ?", (sid_bad,)).fetchone()[0]
        assert err is not None and "RuntimeError" in err
        # Verify the good row flipped.
        flag = db.execute("SELECT pushed_to_neo4j FROM entity_signals WHERE id = ?", (sid_ok,)).fetchone()[0]
        assert flag == 1
    finally:
        db.close()


def test_run_neo4j_drain_tick_skips_relationship_kind_and_bumps_counter() -> None:
    """``kind="relationship"`` increments ``skipped_relationships`` + the attempt counter."""
    db = _open()
    try:
        sid = _insert(db, kind="relationship", value="alpha -> bravo")
        repo = FakeDrainGraphRepository(available=True)
        result = run_neo4j_drain_tick(db, repo)
        assert result.skipped_relationships == 1
        assert result.pushed == 0
        # Counter bumped so the next tick drops the row out of selection.
        counter = db.execute("SELECT push_attempt_count FROM entity_signals WHERE id = ?", (sid,)).fetchone()[0]
        assert counter == 1
    finally:
        db.close()
