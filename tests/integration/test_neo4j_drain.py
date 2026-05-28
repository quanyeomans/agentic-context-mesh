"""Integration tests for the GH #334 Neo4j entity-graph drain.

Composed through :func:`kairix.core.factory.build_neo4j_drainer`
(F47 sanctioned factory entry point). The Neo4j boundary is a
:class:`tests.fakes.FakeDrainGraphRepository` — no real driver.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kairix.core import factory
from kairix.core.curator.drain import DEFAULT_DRAIN_BATCH_SIZE, MAX_PUSH_ATTEMPTS
from kairix.core.db.schema import create_schema
from tests.fakes import FakeDrainGraphRepository

pytestmark = pytest.mark.integration


def _open_db(tmp_path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(str(tmp_path / "drain_integration.sqlite"))
    create_schema(db)
    return db


def _insert(
    db: sqlite3.Connection,
    *,
    kind: str = "person",
    value: str,
    modified_at: str = "2026-05-25T10:00:00Z",
    pushed_to_neo4j: int = 0,
    push_attempt_count: int = 0,
) -> int:
    cur = db.execute(
        "INSERT INTO entity_signals (kind, value, source_uri, modified_at, confidence, "
        "sensitivity, pushed_to_neo4j, push_attempt_count) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (kind, value, f"vault://{value}.md", modified_at, 0.85, "internal", pushed_to_neo4j, push_attempt_count),
    )
    db.commit()
    return int(cur.lastrowid or 0)


def _flag_for(db: sqlite3.Connection, signal_id: int) -> int:
    row = db.execute("SELECT pushed_to_neo4j FROM entity_signals WHERE id = ?", (signal_id,)).fetchone()
    assert row is not None
    return int(row[0])


def test_drain_flips_pushed_flag_within_one_tick(tmp_path: Path) -> None:
    """A reachable Neo4j drains the staged batch in one tick; flag flips."""
    db = _open_db(tmp_path)
    try:
        ids = [_insert(db, value=f"person-{i}") for i in range(3)]
        repo = FakeDrainGraphRepository(available=True)

        drainer = factory.build_neo4j_drainer(db=db, repo=repo)
        result = drainer.tick()

        # Assert on concrete numbers — every signal flipped, repo got the calls.
        assert result.neo4j_available is True
        assert result.pushed == 3, f"expected pushed=3, got {result.pushed} ({result})"
        assert result.failed == 0
        for sid in ids:
            assert _flag_for(db, sid) == 1, f"signal id={sid} should be flipped, got {_flag_for(db, sid)}"
        person_calls = [c for c in repo.cypher_calls if "MERGE (p:Person" in c[0]]
        assert len(person_calls) == 3, f"expected 3 MERGE calls, got {len(person_calls)}"
    finally:
        db.close()


def test_drain_two_consecutive_ticks_drain_full_backlog(tmp_path: Path) -> None:
    """Backlog > batch_size — two ticks drain everything (oldest first)."""
    db = _open_db(tmp_path)
    try:
        # 7 rows, batch_size 5 → tick 1 drains 5 oldest, tick 2 drains the remaining 2.
        for i in range(7):
            _insert(db, value=f"backlog-{i}", modified_at=f"2026-05-2{i}T10:00:00Z")
        repo = FakeDrainGraphRepository(available=True)

        drainer = factory.build_neo4j_drainer(db=db, repo=repo, batch_size=5)
        first = drainer.tick()
        second = drainer.tick()

        assert first.pushed == 5
        assert second.pushed == 2
        unpushed_count = db.execute("SELECT COUNT(*) FROM entity_signals WHERE pushed_to_neo4j = 0").fetchone()[0]
        assert unpushed_count == 0, f"expected zero un-pushed after both ticks, got {unpushed_count}"
    finally:
        db.close()


def test_drain_neo4j_failure_marks_signal_not_flag_pushed(tmp_path: Path) -> None:
    """When the repo raises mid-tick on one row, that row stays un-pushed
    (flag goes to -1, not 1), the OTHER rows still commit, and the
    error text lands on the failed row.
    """
    db = _open_db(tmp_path)
    try:
        sid_a = _insert(db, value="alpha", modified_at="2026-05-20T10:00:00Z")
        sid_b = _insert(db, value="bravo", modified_at="2026-05-21T10:00:00Z")
        sid_c = _insert(db, value="charlie", modified_at="2026-05-22T10:00:00Z")
        repo = FakeDrainGraphRepository(available=True, raise_on_value="bravo")

        drainer = factory.build_neo4j_drainer(db=db, repo=repo)
        result = drainer.tick()

        assert result.pushed == 2, f"expected pushed=2, got {result.pushed}"
        assert result.failed == 1, f"expected failed=1, got {result.failed}"
        # alpha and charlie commit cleanly; bravo lands at -1 with error.
        assert _flag_for(db, sid_a) == 1
        assert _flag_for(db, sid_b) == -1
        assert _flag_for(db, sid_c) == 1
        err_row = db.execute("SELECT last_push_error FROM entity_signals WHERE id = ?", (sid_b,)).fetchone()
        assert err_row is not None and err_row[0] is not None and len(err_row[0]) > 0, (
            f"expected non-empty last_push_error on failed row, got {err_row!r}"
        )
    finally:
        db.close()


def test_drain_relationship_kind_skipped_with_counter(tmp_path: Path) -> None:
    """``kind="relationship"`` signals are skipped this PR; counter increments."""
    db = _open_db(tmp_path)
    try:
        rel_id = _insert(db, kind="relationship", value="alpha -> bravo")
        person_id = _insert(db, kind="person", value="alpha-person")
        repo = FakeDrainGraphRepository(available=True)

        drainer = factory.build_neo4j_drainer(db=db, repo=repo)
        result = drainer.tick()

        assert result.skipped_relationships == 1, (
            f"expected skipped_relationships=1, got {result.skipped_relationships}"
        )
        assert result.pushed == 1, f"expected pushed=1 (the person), got {result.pushed}"
        # Relationship flag stays at 0 (deliberately); attempt-count bumped.
        assert _flag_for(db, rel_id) == 0
        attempt_row = db.execute("SELECT push_attempt_count FROM entity_signals WHERE id = ?", (rel_id,)).fetchone()
        assert attempt_row is not None and int(attempt_row[0]) == 1, (
            f"expected push_attempt_count=1 on skipped relationship, got {attempt_row!r}"
        )
        assert _flag_for(db, person_id) == 1
    finally:
        db.close()


def test_drain_signal_at_max_attempts_skipped(tmp_path: Path) -> None:
    """A row whose ``push_attempt_count >= MAX_PUSH_ATTEMPTS`` drops out of selection.

    Proves the bounded-retry contract — drain doesn't loop forever
    on a structurally-bad row.
    """
    db = _open_db(tmp_path)
    try:
        stalled = _insert(
            db,
            value="stalled",
            modified_at="2026-05-20T10:00:00Z",
            pushed_to_neo4j=-1,
            push_attempt_count=MAX_PUSH_ATTEMPTS,
        )
        fresh = _insert(db, value="fresh", modified_at="2026-05-21T10:00:00Z")
        repo = FakeDrainGraphRepository(available=True)

        drainer = factory.build_neo4j_drainer(db=db, repo=repo)
        result = drainer.tick()

        # Only the fresh row drains; the stalled row is past the cap.
        assert result.pushed == 1, f"expected pushed=1 (fresh only), got {result.pushed}"
        assert _flag_for(db, fresh) == 1
        # Stalled still at -1; no further attempts were made.
        assert _flag_for(db, stalled) == -1
        attempt_row = db.execute("SELECT push_attempt_count FROM entity_signals WHERE id = ?", (stalled,)).fetchone()
        assert attempt_row is not None and int(attempt_row[0]) == MAX_PUSH_ATTEMPTS, (
            f"expected push_attempt_count={MAX_PUSH_ATTEMPTS} unchanged, got {attempt_row!r}"
        )
    finally:
        db.close()


def test_drain_unavailable_neo4j_is_full_noop(tmp_path: Path) -> None:
    """``repo.available == False`` → no row touched; result envelope reports outage."""
    db = _open_db(tmp_path)
    try:
        sid = _insert(db, value="should-stay-pending")
        repo = FakeDrainGraphRepository(available=False)

        drainer = factory.build_neo4j_drainer(db=db, repo=repo)
        result = drainer.tick()

        assert result.neo4j_available is False
        assert result.pushed == 0
        assert _flag_for(db, sid) == 0, "signal flag flipped despite unreachable backend"
        assert repo.cypher_calls == [], f"unreachable backend received cypher calls: {repo.cypher_calls}"
    finally:
        db.close()


def test_drain_default_batch_size_constant() -> None:
    """The published default matches the operator-facing CLI help text."""
    assert DEFAULT_DRAIN_BATCH_SIZE == 500
