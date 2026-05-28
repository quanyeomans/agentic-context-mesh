"""Invariant: every topology_cc_pairs.status transition (across ticks) is in _ALLOWED_TRANSITIONS.

Why
---
ADR-024 §F72 — F57 catches ad-hoc UPDATE statements against
``topology_cc_pairs.status`` *statically* (any module mutating that
column without a sibling ``_ALLOWED_TRANSITIONS`` dispatch trips the
gate at commit time). This invariant extends that contract from
single-tick to multi-tick: after N transition calls, every observed
``status`` value must be reachable from the previous observed value
under :data:`_ALLOWED_TRANSITIONS`. If a row's status jumped from
SCHEDULED to ACTIVE without going through INITIAL_INDEXING, F57 would
miss it (the dispatch table is present; the call path just used the
wrong source state).

The mechanical contract: for every cc_pair row tracked across N ticks,

    forall (prev_status, new_status) observed:
        new_status in _ALLOWED_TRANSITIONS[prev_status]
        OR new_status == prev_status  # idempotent no-op tick

The "OR new_status == prev_status" clause is the multi-tick allowance —
re-reading the row after a no-op tick must not be reported as an
illegal transition.

Sabotage proof (executed during authoring)
------------------------------------------
Mutation: in ``kairix/core/connectors/cc_pair.py::_ALLOWED_TRANSITIONS``,
delete the ``"SCHEDULED": frozenset({_INITIAL_INDEXING, ...})`` entry
or replace its value with ``frozenset()``. Re-run this test:

    CCPairTransitionError: cc_pair transition SCHEDULED → INITIAL_INDEXING
      not allowed: requested via transition_cc_pair

Or, mutation that surfaces THIS invariant rather than the
single-tick F57 surface: bypass ``transition_cc_pair`` by issuing a
raw ``UPDATE topology_cc_pairs SET status = 'ACTIVE' WHERE id = ?``
(simulates code that drifts past the lifecycle service). The
invariant catches the SCHEDULED→ACTIVE jump because ACTIVE is not in
``_ALLOWED_TRANSITIONS["SCHEDULED"]``.

Restoration: revert. The lifecycle service rejects illegal jumps; the
invariant proves a multi-tick sequence stays inside the dispatch table.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kairix.core.connectors.cc_pair import (
    ALLOWED_TRANSITIONS,
    create_cc_pair,
    get_cc_pair,
    transition_cc_pair,
)
from kairix.core.db.schema import create_schema

pytestmark = pytest.mark.invariant


def _open_db(tmp_path: Path, name: str = "cc_pair_lifecycle.sqlite") -> sqlite3.Connection:
    db = sqlite3.connect(str(tmp_path / name))
    create_schema(db, dims=4)
    return db


def _seed_connector(db: sqlite3.Connection, *, kind: str, name: str) -> int:
    """Insert a topology_connectors row and return its id."""
    now = "2026-05-28T14:00:00Z"
    cur = db.execute(
        "INSERT INTO topology_connectors "
        "(kind, name, connector_specific_config, default_sensitivity, created_at, updated_at) "
        "VALUES (?, ?, '{}', 'internal', ?, ?)",
        (kind, name, now, now),
    )
    db.commit()
    assert cur.lastrowid is not None
    return int(cur.lastrowid)


def _drive_lifecycle_path(
    db: sqlite3.Connection,
    cc_pair_id: int,
    path: list[str],
) -> list[str]:
    """Drive a cc_pair through ``path`` and return the observed status sequence.

    The first entry in ``path`` should equal the cc_pair's current
    status (SCHEDULED for a freshly-created row). Each subsequent entry
    is passed to ``transition_cc_pair``. Returns the observed sequence
    (the current status read after each transition call).
    """
    observed: list[str] = []
    current = get_cc_pair(db, cc_pair_id)
    assert current is not None, f"fixture self-check: cc_pair_id={cc_pair_id} missing after create"
    observed.append(current.status)
    for target in path[1:]:
        pair = transition_cc_pair(db, cc_pair_id, target)  # type: ignore[arg-type]  # path entries are CCPairStatus literals at runtime
        db.commit()
        observed.append(pair.status)
    return observed


def _assert_sequence_in_dispatch(observed: list[str], pair_label: str) -> None:
    """Assert every (prev, next) pair in ``observed`` is in _ALLOWED_TRANSITIONS.

    The assertion message names the offending hop so the operator sees
    which transition slipped past the dispatch.
    """
    for i in range(1, len(observed)):
        prev = observed[i - 1]
        nxt = observed[i]
        if prev == nxt:
            # No-op tick is always allowed — the row was re-read without
            # a state change.
            continue
        allowed = ALLOWED_TRANSITIONS.get(prev, frozenset())  # type: ignore[arg-type]  # prev is a runtime str; dispatch keys are CCPairStatus literals
        assert nxt in allowed, (
            f"cc_pair_lifecycle_consistency violated for {pair_label}: "
            f"observed sequence {observed!r} contains illegal hop "
            f"{prev} -> {nxt}. ALLOWED_TRANSITIONS[{prev}] = {sorted(allowed)!r}. "
            f"See ADR-024 §F72 — multi-tick assertion extends F57 from "
            f"single-call static checks to runtime-sequence dynamic checks."
        )


def test_invariant_holds_at_fixture_scale(tmp_path: Path) -> None:
    """N=10 cc_pairs each walked through SCHEDULED → INITIAL_INDEXING → ACTIVE → PAUSED → ACTIVE."""
    db = _open_db(tmp_path)
    try:
        connector_id = _seed_connector(db, kind="obsidian", name="cc-pair-lifecycle-fixture-conn")
        # The canonical happy-path lifecycle — every hop is in
        # _ALLOWED_TRANSITIONS so the invariant must hold.
        happy_path = ["SCHEDULED", "INITIAL_INDEXING", "ACTIVE", "PAUSED", "ACTIVE"]
        for i in range(10):
            pair = create_cc_pair(
                db,
                connector_id=connector_id,
                credential_id=None,
                name=f"cc-pair-{i:02d}",
            )
            db.commit()
            observed = _drive_lifecycle_path(db, pair.id, happy_path)
            assert observed == happy_path, (
                f"fixture self-check: drove cc_pair {pair.id} through {happy_path!r}, observed {observed!r}"
            )
            _assert_sequence_in_dispatch(observed, pair_label=f"cc_pair_id={pair.id}")
        # Final sibling-assert: read every cc_pair and confirm its
        # current status is one the dispatch knows about (sanity-check
        # against silent corruption of the column).
        all_rows = db.execute("SELECT id, status FROM topology_cc_pairs").fetchall()
        assert len(all_rows) == 10, f"fixture self-check: expected 10 cc_pair rows, got {len(all_rows)}"
        for row_id, status in all_rows:
            assert status in ALLOWED_TRANSITIONS, (
                f"cc_pair_lifecycle_consistency: cc_pair id={row_id} has status "
                f"{status!r} which is not a key in ALLOWED_TRANSITIONS "
                f"({sorted(ALLOWED_TRANSITIONS)!r}) — the column drifted"
            )
    finally:
        db.close()


@pytest.mark.soak
def test_invariant_holds_at_soak_scale(tmp_path: Path) -> None:
    """N=10**4 cc_pairs each walked through the happy-path lifecycle.

    The soak run proves the lifecycle service holds at production
    scale — a regression where a per-row transition leaks an
    out-of-dispatch status under sustained load surfaces here as a
    concrete (prev, next) violation hop.
    """
    db = _open_db(tmp_path, name="cc_pair_lifecycle_soak.sqlite")
    try:
        connector_id = _seed_connector(db, kind="obsidian", name="cc-pair-lifecycle-soak-conn")
        happy_path = ["SCHEDULED", "INITIAL_INDEXING", "ACTIVE"]
        n = 10_000
        for i in range(n):
            pair = create_cc_pair(
                db,
                connector_id=connector_id,
                credential_id=None,
                name=f"cc-pair-soak-{i:06d}",
            )
            db.commit()
            observed = _drive_lifecycle_path(db, pair.id, happy_path)
            _assert_sequence_in_dispatch(observed, pair_label=f"cc_pair_id={pair.id}")
        # Final scale check — total rows match the seed.
        total_row = db.execute("SELECT COUNT(*) FROM topology_cc_pairs").fetchone()
        total = int(total_row[0]) if total_row else 0
        assert total == n, f"soak self-check: expected {n} cc_pair rows, got {total}"
    finally:
        db.close()
