"""Contract tests for the cc_pair lifecycle service (ADR v2 §3 state machine).

Pins:
* Every valid transition in ``ALLOWED_TRANSITIONS`` succeeds.
* Every other edge raises :exc:`CCPairTransitionError`.
* The ``DELETING`` terminal state has no outbound edges.
* ``transition_cc_pair`` stamps the appropriate ``last_*_at`` column.
* ``create_cc_pair`` lands rows at ``status=SCHEDULED``.
* F57 baseline stays empty (every UPDATE on topology_cc_pairs.status
  lives in a module that declares ``ALLOWED_TRANSITIONS``).

Sabotage-prove targets (per feedback_sabotage_must_be_executed):
- INVALID → ACTIVE should raise (mutate ALLOWED_TRANSITIONS to allow it
  → confirm test_illegal_transition_raises fails → restore)
"""

from __future__ import annotations

import sqlite3

import pytest

from kairix.core.connectors.cc_pair import (
    ALLOWED_TRANSITIONS,
    create_cc_pair,
    get_cc_pair,
    list_cc_pairs,
    transition_cc_pair,
)
from kairix.core.db.schema import create_schema
from kairix.core.protocols import CCPairStatus, CCPairTransitionError

pytestmark = pytest.mark.contract


_ALL_STATUSES: tuple[CCPairStatus, ...] = (
    "SCHEDULED",
    "INITIAL_INDEXING",
    "ACTIVE",
    "PAUSED",
    "DELETING",
    "INVALID",
)


def _fresh_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    create_schema(db, dims=4)
    # Seed a connector so we can create cc_pairs that reference a real FK.
    now = "2026-05-23T00:00:00Z"
    db.execute(
        "INSERT INTO topology_connectors "
        "(kind, name, connector_specific_config, default_sensitivity, created_at, updated_at) "
        "VALUES ('obsidian', 'connector-alpha', '{}', 'internal', ?, ?)",
        (now, now),
    )
    db.commit()
    return db


def _connector_id(db: sqlite3.Connection) -> int:
    row = db.execute("SELECT id FROM topology_connectors WHERE name = 'connector-alpha'").fetchone()
    return int(row[0])


def _force_status(db: sqlite3.Connection, cc_pair_id: int, status: CCPairStatus) -> None:
    """Manually shove a status for setup purposes (bypasses transition validation).

    Used ONLY to set up the precondition for a "test illegal transition
    from <state>" case. Production code goes through transition_cc_pair.
    This is the only place in the test suite that does a raw UPDATE; the
    rest of the test surface uses transition_cc_pair as the public API.

    F57: this UPDATE lives in a test file (tests/contracts/), not under
    kairix/, so the F57 scan does not look at it.
    """
    db.execute("UPDATE topology_cc_pairs SET status = ? WHERE id = ?", (status, cc_pair_id))
    db.commit()


def test_create_cc_pair_lands_at_scheduled() -> None:
    db = _fresh_db()
    pair = create_cc_pair(
        db,
        connector_id=_connector_id(db),
        credential_id=None,
        name="cc-alpha",
    )
    db.commit()
    assert pair.status == "SCHEDULED"
    assert pair.name == "cc-alpha"
    assert pair.in_repeated_error_state is False
    assert pair.total_docs_indexed == 0


def test_get_cc_pair_returns_none_for_missing() -> None:
    db = _fresh_db()
    assert get_cc_pair(db, 9999) is None


def test_list_cc_pairs_filters_by_status() -> None:
    db = _fresh_db()
    a = create_cc_pair(db, connector_id=_connector_id(db), credential_id=None, name="cc-a")
    b = create_cc_pair(db, connector_id=_connector_id(db), credential_id=None, name="cc-b")
    db.commit()
    transition_cc_pair(db, a.id, "INITIAL_INDEXING")
    transition_cc_pair(db, a.id, "ACTIVE")
    db.commit()
    active = list_cc_pairs(db, status="ACTIVE")
    scheduled = list_cc_pairs(db, status="SCHEDULED")
    all_pairs = list_cc_pairs(db)
    assert {p.name for p in active} == {"cc-a"}
    assert {p.name for p in scheduled} == {"cc-b"}
    assert {p.name for p in all_pairs} == {"cc-a", "cc-b"}
    assert b.id != a.id


@pytest.mark.parametrize(
    "current,target",
    [(current, target) for current, allowed in ALLOWED_TRANSITIONS.items() for target in allowed],
)
def test_every_allowed_transition_succeeds(current: CCPairStatus, target: CCPairStatus) -> None:
    """Each (current, target) edge in ALLOWED_TRANSITIONS works without raising."""
    db = _fresh_db()
    pair = create_cc_pair(db, connector_id=_connector_id(db), credential_id=None, name=f"cc-{current}-{target}")
    db.commit()
    _force_status(db, pair.id, current)
    result = transition_cc_pair(db, pair.id, target)
    db.commit()
    assert result.status == target


@pytest.mark.parametrize(
    "current,target",
    [
        (current, target)
        for current in ALLOWED_TRANSITIONS
        for target in _ALL_STATUSES
        if target not in ALLOWED_TRANSITIONS[current]
    ],
)
def test_illegal_transition_raises(current: CCPairStatus, target: CCPairStatus) -> None:
    """Every disallowed edge raises CCPairTransitionError carrying current/target."""
    db = _fresh_db()
    pair = create_cc_pair(db, connector_id=_connector_id(db), credential_id=None, name=f"cc-{current}-{target}")
    db.commit()
    _force_status(db, pair.id, current)
    with pytest.raises(CCPairTransitionError) as excinfo:
        transition_cc_pair(db, pair.id, target)
    assert excinfo.value.current == current
    assert excinfo.value.target == target


def test_deleting_is_terminal() -> None:
    """DELETING has no outbound edges — every other status is unreachable."""
    assert ALLOWED_TRANSITIONS["DELETING"] == frozenset()
    db = _fresh_db()
    pair = create_cc_pair(db, connector_id=_connector_id(db), credential_id=None, name="cc-term")
    db.commit()
    _force_status(db, pair.id, "DELETING")
    for target in _ALL_STATUSES:
        with pytest.raises(CCPairTransitionError):
            transition_cc_pair(db, pair.id, target)


def test_transition_to_active_stamps_last_successful_index_time() -> None:
    """SCHEDULED → INITIAL_INDEXING → ACTIVE stamps last_successful_index_time."""
    db = _fresh_db()
    pair = create_cc_pair(db, connector_id=_connector_id(db), credential_id=None, name="cc-stamp")
    db.commit()
    assert pair.last_successful_index_time is None
    transition_cc_pair(db, pair.id, "INITIAL_INDEXING")
    intermediate = get_cc_pair(db, pair.id)
    assert intermediate is not None
    assert intermediate.last_successful_index_time is None  # not stamped on this transition
    transition_cc_pair(db, pair.id, "ACTIVE")
    final = get_cc_pair(db, pair.id)
    assert final is not None
    assert final.last_successful_index_time is not None


def test_missing_cc_pair_id_raises_with_diagnostic() -> None:
    """transition_cc_pair on a missing id raises with a useful 'reason'."""
    db = _fresh_db()
    with pytest.raises(CCPairTransitionError) as excinfo:
        transition_cc_pair(db, 9999, "SCHEDULED")
    assert "9999" in (excinfo.value.reason or "")
