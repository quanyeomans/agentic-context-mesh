"""cc_pair lifecycle service — ADR v2 §3 state machine, F57-centralised.

The cc_pair (ConnectorCredentialPair) is the operational unit that
binds a :class:`~kairix.core.protocols.ConnectorInstance` and a
:class:`~kairix.core.protocols.Credential`. Its ``status`` column on
``topology_cc_pairs`` is a strict state machine; this module owns the
ONLY allowed transitions per F57 (every other module that mutates
``topology_cc_pairs.status`` trips ``check_f57_ccpair_lifecycle_integrity``).

Public API:

* :func:`create_cc_pair` — INSERT a fresh row, ``status=SCHEDULED``.
* :func:`transition_cc_pair` — validate against ``_ALLOWED_TRANSITIONS``,
  rewrite ``status``, stamp the appropriate ``last_*_at`` field.
* :func:`get_cc_pair` — read one row.
* :func:`list_cc_pairs` — read all (optionally filtered by status).

State machine (per ADR v2 §3):

  SCHEDULED        → INITIAL_INDEXING | DELETING | INVALID
  INITIAL_INDEXING → ACTIVE           | INVALID  | DELETING
  ACTIVE           → PAUSED           | DELETING | INVALID
  PAUSED           → ACTIVE           | DELETING
  DELETING         → (terminal)
  INVALID          → SCHEDULED        | DELETING       (operator can re-schedule)

Illegal jumps raise :exc:`CCPairTransitionError` (defined in
``kairix.core.protocols``).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from kairix.core.protocols import (
    CCPairAccessType,
    CCPairStatus,
    CCPairTransitionError,
    ConnectorCredentialPair,
)

# ---------------------------------------------------------------------------
# State machine — the dispatch table F57 looks for at module level.
# ---------------------------------------------------------------------------

# F17 — extract the repeated literal so adding a status doesn't churn
# the string across the dispatch table and the per-status timestamp map.
_INITIAL_INDEXING: CCPairStatus = "INITIAL_INDEXING"

_ALLOWED_TRANSITIONS: dict[CCPairStatus, frozenset[CCPairStatus]] = {
    "SCHEDULED": frozenset({_INITIAL_INDEXING, "DELETING", "INVALID"}),
    _INITIAL_INDEXING: frozenset({"ACTIVE", "INVALID", "DELETING"}),
    "ACTIVE": frozenset({"PAUSED", "DELETING", "INVALID"}),
    "PAUSED": frozenset({"ACTIVE", "DELETING"}),
    "DELETING": frozenset(),
    "INVALID": frozenset({"SCHEDULED", "DELETING"}),
}

# Public read-only alias — tests and external observability surfaces
# consume this name, not the underscored canonical (which F57 watches).
ALLOWED_TRANSITIONS = _ALLOWED_TRANSITIONS


def _now() -> str:
    """ISO-8601 UTC timestamp string (matches every other topology table)."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _row_to_cc_pair(row: tuple[Any, ...]) -> ConnectorCredentialPair:
    """Map an ``topology_cc_pairs`` row tuple to the frozen dataclass."""
    return ConnectorCredentialPair(
        id=row[0],
        connector_id=row[1],
        credential_id=row[2],
        name=row[3],
        access_type=row[4],
        status=row[5],
        last_successful_index_time=row[6],
        last_time_perm_sync=row[7],
        last_time_external_group_sync=row[8],
        last_time_hierarchy_fetch=row[9],
        in_repeated_error_state=bool(row[10]),
        total_docs_indexed=row[11],
        refresh_freq_override_seconds=row[12],
        prune_freq_override_seconds=row[13],
        created_at=row[14],
        updated_at=row[15],
    )


_SELECT_ALL_COLUMNS = (
    "id, connector_id, credential_id, name, access_type, status, "
    "last_successful_index_time, last_time_perm_sync, "
    "last_time_external_group_sync, last_time_hierarchy_fetch, "
    "in_repeated_error_state, total_docs_indexed, "
    "refresh_freq_override_seconds, prune_freq_override_seconds, "
    "created_at, updated_at"
)


def create_cc_pair(
    db: sqlite3.Connection,
    *,
    connector_id: int,
    credential_id: int | None,
    name: str,
    access_type: CCPairAccessType = "PRIVATE",
) -> ConnectorCredentialPair:
    """INSERT a fresh cc_pair row at ``status=SCHEDULED`` and return it.

    Caller owns the commit — matches the rest of the topology surface.
    """
    now = _now()
    cur = db.execute(
        "INSERT INTO topology_cc_pairs "
        "(connector_id, credential_id, name, access_type, status, "
        "in_repeated_error_state, total_docs_indexed, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'SCHEDULED', 0, 0, ?, ?)",
        (connector_id, credential_id, name, access_type, now, now),
    )
    new_id = cur.lastrowid
    if new_id is None:
        raise RuntimeError(f"failed to INSERT topology_cc_pairs row for name={name!r}")
    fetched = get_cc_pair(db, new_id)
    if fetched is None:
        raise RuntimeError(f"failed to read back topology_cc_pairs row id={new_id}")
    return fetched


def get_cc_pair(db: sqlite3.Connection, cc_pair_id: int) -> ConnectorCredentialPair | None:
    """Return the cc_pair row for ``cc_pair_id`` or ``None`` if missing."""
    row = db.execute(
        f"SELECT {_SELECT_ALL_COLUMNS} FROM topology_cc_pairs WHERE id = ?",
        (cc_pair_id,),
    ).fetchone()
    return None if row is None else _row_to_cc_pair(row)


def list_cc_pairs(
    db: sqlite3.Connection,
    *,
    status: CCPairStatus | None = None,
) -> tuple[ConnectorCredentialPair, ...]:
    """Return every cc_pair row (optionally filtered by ``status``)."""
    if status is None:
        # F63-bounded: topology_cc_pairs is operator-config-sized (≤O(100) rows).
        rows = db.execute(f"SELECT {_SELECT_ALL_COLUMNS} FROM topology_cc_pairs ORDER BY id").fetchall()
    else:
        # F63-bounded: topology_cc_pairs is operator-config-sized; additionally filtered by status.
        rows = db.execute(
            f"SELECT {_SELECT_ALL_COLUMNS} FROM topology_cc_pairs WHERE status = ? ORDER BY id",
            (status,),
        ).fetchall()
    return tuple(_row_to_cc_pair(r) for r in rows)


_STATUS_TO_TIMESTAMP_COLUMN: dict[CCPairStatus, str | None] = {
    "SCHEDULED": None,
    _INITIAL_INDEXING: None,
    "ACTIVE": "last_successful_index_time",
    "PAUSED": None,
    "DELETING": None,
    "INVALID": None,
}


def transition_cc_pair(
    db: sqlite3.Connection,
    cc_pair_id: int,
    target_status: CCPairStatus,
    *,
    reason: str | None = None,
) -> ConnectorCredentialPair:
    """Validate + apply a status transition on ``topology_cc_pairs``.

    Raises :exc:`CCPairTransitionError` when ``current → target_status``
    is not in :data:`_ALLOWED_TRANSITIONS`. On success stamps the
    appropriate ``last_*_at`` column (per :data:`_STATUS_TO_TIMESTAMP_COLUMN`)
    AND the always-present ``updated_at`` column. Caller owns the commit.

    Marshals every status mutation through one call site so F57 stays
    satisfied — any other module issuing ``UPDATE topology_cc_pairs SET
    status`` would fail the pre-commit gate (the gate looks for a
    sibling ``_ALLOWED_TRANSITIONS`` table in the same module).
    """
    current_row = db.execute("SELECT status FROM topology_cc_pairs WHERE id = ?", (cc_pair_id,)).fetchone()
    if current_row is None:
        raise CCPairTransitionError("MISSING", target_status, reason=f"cc_pair_id={cc_pair_id} not found")
    current: CCPairStatus = current_row[0]
    allowed = _ALLOWED_TRANSITIONS.get(current, frozenset())
    if target_status not in allowed:
        raise CCPairTransitionError(current, target_status, reason=reason)

    now = _now()
    stamp_column = _STATUS_TO_TIMESTAMP_COLUMN.get(target_status)
    if stamp_column is None:
        db.execute(
            "UPDATE topology_cc_pairs SET status = ?, updated_at = ? WHERE id = ?",
            (target_status, now, cc_pair_id),
        )
    else:
        # safe: stamp_column is from a closed allow-list above; no operator input.
        db.execute(
            f"UPDATE topology_cc_pairs SET status = ?, {stamp_column} = ?, updated_at = ? WHERE id = ?",
            (target_status, now, now, cc_pair_id),
        )
    fetched = get_cc_pair(db, cc_pair_id)
    if fetched is None:
        raise RuntimeError(f"cc_pair id={cc_pair_id} vanished after UPDATE")
    return fetched


def cc_pair_lifecycle_audit_blob(pair: ConnectorCredentialPair) -> str:
    """Compact JSON blob suitable for audit logs / operator reports.

    Separate from the dataclass repr so the audit shape is stable across
    refactors of :class:`ConnectorCredentialPair`. Used by Wave D operator
    CLI verbs that surface lifecycle history.
    """
    return json.dumps(
        {
            "id": pair.id,
            "name": pair.name,
            "status": pair.status,
            "access_type": pair.access_type,
            "in_repeated_error_state": pair.in_repeated_error_state,
            "total_docs_indexed": pair.total_docs_indexed,
            "last_successful_index_time": pair.last_successful_index_time,
            "updated_at": pair.updated_at,
        },
        sort_keys=True,
    )
