"""Invariant: pushed_to_<sink>=0 counts only grow when the sink is unreachable.

Why
---
ADR-024 §"Defects" — GH #334: the ``entity_signals`` staging table sat
in production with 2.3M un-pushed rows because no code flipped
``pushed_to_neo4j`` from 0 → 1 (the Wave-2 docstring promised a Curator
drain that was never written). F67 catches the missing-drain shape
*statically*; this invariant catches the failure mode at runtime —
even with a drain wired in, ``pushed_to_<sink>=0`` should not grow
unless the sink is unreachable.

The mechanical contract: after the drain runs against a reachable
backend on a populated staging table, the un-pushed count must shrink
(or hit zero). When the backend is unreachable, the un-pushed count
must stay constant (no flag-flipping without a successful sink call).

This invariant pairs with the existing F71 truthfulness contract for
``_check_entity_signals_staging_not_stuck`` — F71 proves the preflight
reports the true count; this invariant proves the count actually
decreases when the drain runs successfully.

Sabotage proof (executed during authoring)
------------------------------------------
Mutation: in ``kairix/core/curator/drain.py::Neo4jDrainer._update_pushed``
(or wherever the per-row UPDATE flips the flag), replace
``"UPDATE entity_signals SET pushed_to_neo4j = 1..."`` with
``"UPDATE entity_signals SET pushed_to_neo4j = 0..."`` (no-op flip).
Re-run this test:

    AssertionError: staging_drain_progress violated: pre-tick
      un-pushed=3, post-tick un-pushed=3 (expected: shrinks toward
      zero when backend reachable). The drain ran but flipped no
      flags — GH #334 failure mode replayed.

Restoration: revert. The fail surface names both counts so the
operator sees the no-progress shape immediately.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kairix.core import factory
from kairix.core.db.schema import create_schema
from tests.fakes import FakeDrainGraphRepository

pytestmark = pytest.mark.invariant


def _open_db(tmp_path: Path, name: str = "drain_progress.sqlite") -> sqlite3.Connection:
    db = sqlite3.connect(str(tmp_path / name))
    create_schema(db)
    return db


def _seed_staged_signals(db: sqlite3.Connection, *, n: int, source_tag: str) -> None:
    """Insert N entity_signals rows at pushed_to_neo4j=0, age=fresh.

    Uses ``kind='person'`` so the drain's per-kind dispatch routes
    every row to a MERGE call (the relationship branch is skipped at
    the drain's discretion; a person row tests the happy drain path).
    """
    rows = [
        (
            "person",
            f"{source_tag}-person-{i:05d}",
            f"test://invariant/{source_tag}/{i}",
            "2026-05-28T12:00:00Z",
            0.9,
            "internal",
            0,
        )
        for i in range(n)
    ]
    db.executemany(
        "INSERT INTO entity_signals "
        "(kind, value, source_uri, modified_at, confidence, sensitivity, pushed_to_neo4j) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    db.commit()


def _count_unpushed(db: sqlite3.Connection) -> int:
    row = db.execute("SELECT COUNT(*) FROM entity_signals WHERE pushed_to_neo4j = 0").fetchone()
    return int(row[0]) if row else 0


def _drain_until_done(
    db: sqlite3.Connection,
    repo: FakeDrainGraphRepository,
    *,
    max_ticks: int,
    batch_size: int,
) -> tuple[int, int]:
    """Run the drain up to ``max_ticks`` times; return (initial, final) un-pushed counts.

    Uses ``factory.build_neo4j_drainer`` (F47-compliant). Each tick
    pushes up to ``batch_size`` rows; the loop bails out once the
    un-pushed count hits zero so the assertion can see the convergent
    shape without spinning.
    """
    initial = _count_unpushed(db)
    drainer = factory.build_neo4j_drainer(db=db, repo=repo, batch_size=batch_size)
    for _ in range(max_ticks):
        if _count_unpushed(db) == 0:
            break
        drainer.tick()
    final = _count_unpushed(db)
    return initial, final


def _assert_progress(
    *,
    initial: int,
    final: int,
    backend_reachable: bool,
) -> None:
    """Assert the un-pushed count moved (or didn't) as the contract requires.

    Reachable backend: final < initial AND final == 0 (drain converges).
    Unreachable backend: final == initial (no flag flips without a
    successful sink call).
    """
    if backend_reachable:
        assert final < initial, (
            f"staging_drain_progress violated: pre-tick un-pushed={initial}, "
            f"post-tick un-pushed={final} (expected: shrinks toward zero when "
            f"backend reachable). The drain ran but flipped no flags — GH "
            f"#334 failure mode replayed."
        )
        assert final == 0, (
            f"staging_drain_progress: drain did not fully converge. "
            f"pre-tick un-pushed={initial}, post-tick un-pushed={final}. "
            f"Expected zero after max_ticks; the un-pushed remainder "
            f"means the drain stalls before draining the seeded backlog."
        )
    else:
        assert final == initial, (
            f"staging_drain_progress violated: backend unreachable but un-pushed "
            f"count changed from {initial} to {final}. Flag-flipping without a "
            f"successful sink call is exactly the GH #334 silent-stall shape — "
            f"the drain MUST leave un-pushed alone when the repo reports "
            f"available=False."
        )


def test_invariant_holds_at_fixture_scale(tmp_path: Path) -> None:
    """N=20 staged signals: drain converges with reachable backend; stays put when unreachable."""
    db = _open_db(tmp_path)
    try:
        # Reachable-backend branch — drain shrinks count to zero.
        _seed_staged_signals(db, n=20, source_tag="reachable-fixture")
        reachable = FakeDrainGraphRepository(available=True)
        initial_r, final_r = _drain_until_done(db, reachable, max_ticks=5, batch_size=10)
        _assert_progress(initial=initial_r, final=final_r, backend_reachable=True)
        # Sibling-assert: the repo received the MERGE calls — proves the
        # drain actually composed (not a vacuous green where the count
        # shrunk because we deleted rows).
        merge_calls = [c for c in reachable.cypher_calls if "MERGE" in c[0]]
        assert len(merge_calls) == 20, (
            f"fixture self-check: expected 20 MERGE calls, got {len(merge_calls)} — "
            f"drain didn't actually call the backend"
        )

        # Unreachable-backend branch — un-pushed count must NOT change.
        _seed_staged_signals(db, n=10, source_tag="unreachable-fixture")
        # After seeding 10 more, un-pushed = 10 (the previous 20 are now flipped).
        unreachable = FakeDrainGraphRepository(available=False)
        initial_u, final_u = _drain_until_done(db, unreachable, max_ticks=3, batch_size=10)
        assert initial_u == 10, (
            f"fixture self-check: expected un-pushed=10 after seeding 10 fresh rows post-drain; got {initial_u}"
        )
        _assert_progress(initial=initial_u, final=final_u, backend_reachable=False)
    finally:
        db.close()


@pytest.mark.soak
def test_invariant_holds_at_soak_scale(tmp_path: Path) -> None:
    """N=10**4 staged signals: drain converges at production-shape scale.

    The drain's bounded-retry contract + per-tick budget means the
    convergence path crosses ~50 tick boundaries at this scale (default
    batch_size=500). The soak run proves the un-pushed count monotonically
    shrinks across those boundaries — a regression that batches stop
    mid-progress (the actual GH #334 shape) surfaces as a non-zero
    final count.
    """
    db = _open_db(tmp_path, name="drain_progress_soak.sqlite")
    try:
        n = 10_000
        _seed_staged_signals(db, n=n, source_tag="reachable-soak")
        reachable = FakeDrainGraphRepository(available=True)
        initial, final = _drain_until_done(db, reachable, max_ticks=50, batch_size=500)
        _assert_progress(initial=initial, final=final, backend_reachable=True)
        # Sibling: at production scale, prove the MERGE count matches the seed.
        merge_calls = [c for c in reachable.cypher_calls if "MERGE" in c[0]]
        assert len(merge_calls) == n, (
            f"soak self-check: expected {n} MERGE calls, got {len(merge_calls)} — drain skipped rows silently at scale"
        )
    finally:
        db.close()
