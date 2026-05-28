"""F62-style multi-tick idempotency test for the Neo4j drainer (GH #334).

Runs :func:`Neo4jDrainer.tick` twice; asserts that tick 2 with no new
staged signals returns ``pushed=0``, ``failed=0``,
``skipped_relationships=0`` — i.e. genuine idempotency. The drain
must not re-push rows whose ``pushed_to_neo4j`` is already 1, and it
must not increment the per-row attempt counter on an already-pushed
row.

Today F62's scanner doesn't include ``kairix/core/curator/`` so this
test is forward-armed: when F62 is extended to the curator tree, this
file already satisfies the naming convention
(``test_*neo4j_drainer_advance.py``).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kairix.core import factory
from kairix.core.db.schema import create_schema
from tests.fakes import FakeDrainGraphRepository

pytestmark = pytest.mark.integration


def _open(tmp_path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(str(tmp_path / "neo4j_drain_advance.sqlite"))
    create_schema(db)
    return db


def _insert(db: sqlite3.Connection, value: str, modified_at: str) -> int:
    cur = db.execute(
        "INSERT INTO entity_signals (kind, value, source_uri, modified_at, confidence, "
        "sensitivity, pushed_to_neo4j, push_attempt_count) "
        "VALUES (?, ?, ?, ?, ?, ?, 0, 0)",
        ("person", value, f"vault://{value}.md", modified_at, 0.9, "internal"),
    )
    db.commit()
    return int(cur.lastrowid or 0)


def test_neo4j_drainer_advance_tick_two_does_zero_work_when_no_new_signals(
    tmp_path: Path,
) -> None:
    """Tick 1 drains; tick 2 with no new staged rows does nothing."""
    db = _open(tmp_path)
    try:
        sid_a = _insert(db, "alpha", "2026-05-20T10:00:00Z")
        sid_b = _insert(db, "bravo", "2026-05-21T10:00:00Z")
        repo = FakeDrainGraphRepository(available=True)
        drainer = factory.build_neo4j_drainer(db=db, repo=repo)

        first = drainer.tick()
        assert first.pushed == 2, f"tick 1 should drain both, got pushed={first.pushed}"
        # Capture per-row attempt counts so tick 2 can assert no further bump.
        attempt_a_after_t1 = int(
            db.execute("SELECT push_attempt_count FROM entity_signals WHERE id = ?", (sid_a,)).fetchone()[0]
        )
        attempt_b_after_t1 = int(
            db.execute("SELECT push_attempt_count FROM entity_signals WHERE id = ?", (sid_b,)).fetchone()[0]
        )
        assert attempt_a_after_t1 == 1
        assert attempt_b_after_t1 == 1

        # ---- Tick 2 — no new input ---------------------------------------
        cypher_calls_before_t2 = len(repo.cypher_calls)
        second = drainer.tick()
        # Idempotency contract: zero work.
        assert second.pushed == 0, f"tick 2 should be a no-op, got pushed={second.pushed}"
        assert second.failed == 0
        assert second.skipped_relationships == 0
        # Repo got zero new cypher calls on tick 2.
        assert len(repo.cypher_calls) == cypher_calls_before_t2, (
            f"tick 2 made fresh cypher calls: before={cypher_calls_before_t2}, after={len(repo.cypher_calls)}"
        )
        # Attempt counters unchanged.
        attempt_a_after_t2 = int(
            db.execute("SELECT push_attempt_count FROM entity_signals WHERE id = ?", (sid_a,)).fetchone()[0]
        )
        attempt_b_after_t2 = int(
            db.execute("SELECT push_attempt_count FROM entity_signals WHERE id = ?", (sid_b,)).fetchone()[0]
        )
        assert attempt_a_after_t2 == attempt_a_after_t1, "attempt counter bumped on already-pushed row"
        assert attempt_b_after_t2 == attempt_b_after_t1, "attempt counter bumped on already-pushed row"
    finally:
        db.close()


def test_neo4j_drainer_advance_tick_two_drains_only_newly_staged_rows(
    tmp_path: Path,
) -> None:
    """Tick 2 with a freshly-staged row drains only that row, not tick-1 rows."""
    db = _open(tmp_path)
    try:
        sid_existing = _insert(db, "existing", "2026-05-20T10:00:00Z")
        repo = FakeDrainGraphRepository(available=True)
        drainer = factory.build_neo4j_drainer(db=db, repo=repo)

        first = drainer.tick()
        assert first.pushed == 1

        # Stage a new row AFTER tick 1, then run tick 2.
        sid_new = _insert(db, "freshly-staged", "2026-05-22T10:00:00Z")
        cypher_calls_before_t2 = len(repo.cypher_calls)
        second = drainer.tick()

        assert second.pushed == 1, f"tick 2 should drain only the new row, got pushed={second.pushed}"
        # Exactly one new cypher call (for the new row, not the existing one).
        new_calls = repo.cypher_calls[cypher_calls_before_t2:]
        assert len(new_calls) == 1, f"expected 1 fresh cypher call on tick 2, got {len(new_calls)}"
        # Verify the new call was for the freshly-staged row.
        assert new_calls[0][1]["value"] == "freshly-staged", f"tick 2 cypher targeted the wrong row: {new_calls[0]}"
        # Both rows now flipped.
        flag_existing = int(
            db.execute("SELECT pushed_to_neo4j FROM entity_signals WHERE id = ?", (sid_existing,)).fetchone()[0]
        )
        flag_new = int(db.execute("SELECT pushed_to_neo4j FROM entity_signals WHERE id = ?", (sid_new,)).fetchone()[0])
        assert flag_existing == 1
        assert flag_new == 1
    finally:
        db.close()
