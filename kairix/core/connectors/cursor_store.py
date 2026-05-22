"""CursorStore - resumable cursor management for the connector pipeline.

Persists the opaque resumption token a connector hands back from
:meth:`~kairix.core.protocols.SourceConnector.list_changes`. The cursor
advances ONLY on successful batch commit per
:mod:`kairix.core.connectors.pipeline`; on rollback the previous cursor
value stays in place and the next worker tick retries the same range.

Storage is the ``connector_cursors`` table (see schema in spec doc §7).

The store accepts a :class:`sqlite3.Connection` — not a path — because the
**caller owns the transaction**. Read / write issue SQL but never call
``commit()`` or ``rollback()``; that's the per-batch orchestrator's job
(`kairix.core.connectors.pipeline`). This way cursor advance and chunk
writes commit atomically: a crash mid-batch rolls back cleanly and the
next tick replays the same range.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from kairix.core.protocols import Cursor


class CursorStore:
    """SQLite-backed cursor persistence for the connector pipeline.

    The connection passed in at construction is shared with the rest of
    the per-batch transaction — Bronze / Silver / index writes. Neither
    :meth:`read` nor :meth:`write` calls ``commit()`` or ``rollback()``;
    the caller's transaction owns the commit.
    """

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def read(self, source_name: str) -> Cursor | None:
        """Return the stored cursor token for ``source_name`` or ``None``.

        ``None`` means "first sync — start from the beginning of the
        source's change stream".
        """
        row = self._db.execute(
            "SELECT cursor_token FROM connector_cursors WHERE source_name = ?",
            (source_name,),
        ).fetchone()
        if row is None:
            return None
        return str(row[0])

    def write(self, source_name: str, token: Cursor) -> None:
        """UPSERT the cursor token for ``source_name``.

        Does NOT commit — the caller's per-batch transaction owns the
        commit. ``updated_at`` is ISO-8601 UTC.
        """
        now = datetime.now(timezone.utc).isoformat()
        self._db.execute(
            "INSERT OR REPLACE INTO connector_cursors (source_name, cursor_token, updated_at) VALUES (?, ?, ?)",
            (source_name, token, now),
        )
