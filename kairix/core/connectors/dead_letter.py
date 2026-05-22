"""DeadLetterStore - records items the connector pipeline could not process.

After the configured retry count, a failed item is recorded in the
``connector_deadletter`` table (see schema in spec doc §7) and the
cursor advances past it so sibling items proceed. Operators inspect
the dead-letter table to triage source-side problems (auth failure,
unreadable bytes, format outside the escalation chain).

The store accepts a :class:`sqlite3.Connection` — not a path — because
the **caller owns the transaction**. :meth:`record` issues SQL but never
calls ``commit()`` or ``rollback()``; that's the per-batch orchestrator's
job (`kairix.core.connectors.pipeline`). Cursor advance and dead-letter
row writes commit atomically with the rest of the batch.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class DeadLetterEntry:
    """One dead-letter row, surfaced for operator review.

    Frozen per F42 — value object crossing the store boundary.
    """

    source_name: str
    item_id: str
    failure_count: int
    last_error: str
    last_attempt: str


class DeadLetterStore:
    """SQLite-backed dead-letter recorder for the connector pipeline.

    Tracks ``(source_name, item_id)`` failures with a monotonically
    increasing ``failure_count``. Past a configurable threshold (default
    3) an item is considered "poisoned" and the orchestrator should
    advance the cursor past it so sibling items proceed.
    """

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def record(self, source_name: str, item_id: str, error: str) -> int:
        """UPSERT a dead-letter entry; return the resulting ``failure_count``.

        On first failure for ``(source_name, item_id)`` inserts a row with
        ``failure_count=1``. On repeat failure increments the existing
        count and refreshes ``last_error`` / ``last_attempt``.

        Does NOT commit — the caller's per-batch transaction owns the
        commit.
        """
        now = datetime.now(timezone.utc).isoformat()
        existing = self._db.execute(
            "SELECT failure_count FROM connector_deadletter WHERE source_name = ? AND item_id = ?",
            (source_name, item_id),
        ).fetchone()
        if existing is None:
            self._db.execute(
                "INSERT INTO connector_deadletter "
                "(source_name, item_id, failure_count, last_error, last_attempt) "
                "VALUES (?, ?, 1, ?, ?)",
                (source_name, item_id, error, now),
            )
            return 1
        new_count = int(existing[0]) + 1
        self._db.execute(
            "UPDATE connector_deadletter "
            "SET failure_count = ?, last_error = ?, last_attempt = ? "
            "WHERE source_name = ? AND item_id = ?",
            (new_count, error, now, source_name, item_id),
        )
        return new_count

    def is_poisoned(
        self,
        source_name: str,
        item_id: str,
        threshold: int = 3,
    ) -> bool:
        """Return ``True`` when this item has failed ``threshold`` or more times."""
        row = self._db.execute(
            "SELECT failure_count FROM connector_deadletter WHERE source_name = ? AND item_id = ?",
            (source_name, item_id),
        ).fetchone()
        if row is None:
            return False
        return int(row[0]) >= threshold

    def list(self, source_name: str | None = None) -> tuple[DeadLetterEntry, ...]:
        """Return dead-letter entries for operator review.

        When ``source_name`` is given, restrict to that source. Otherwise
        returns every entry. Ordered by ``last_attempt`` ascending so the
        oldest failure surfaces first.
        """
        if source_name is None:
            rows = self._db.execute(
                "SELECT source_name, item_id, failure_count, last_error, last_attempt "
                "FROM connector_deadletter ORDER BY last_attempt ASC"
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT source_name, item_id, failure_count, last_error, last_attempt "
                "FROM connector_deadletter WHERE source_name = ? "
                "ORDER BY last_attempt ASC",
                (source_name,),
            ).fetchall()
        return tuple(
            DeadLetterEntry(
                source_name=str(row[0]),
                item_id=str(row[1]),
                failure_count=int(row[2]),
                last_error=str(row[3]) if row[3] is not None else "",
                last_attempt=str(row[4]),
            )
            for row in rows
        )
