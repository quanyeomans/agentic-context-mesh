"""CursorStore - resumable cursor management for the connector pipeline.

Persists the opaque resumption token a connector hands back from
:meth:`~kairix.core.protocols.SourceConnector.list_changes`. The cursor
advances ONLY on successful batch commit per
:mod:`kairix.core.connectors.pipeline`; on rollback the previous cursor
value stays in place and the next worker tick retries the same range.

Storage is the ``connector_cursors`` table (see schema in spec doc §7).

Method bodies are intentionally single-statement
``raise NotImplementedError`` calls so the F19 unused-parameter rule
recognises them as abstract-style skeletons.
"""

from __future__ import annotations

from kairix.core.protocols import Cursor


class CursorStore:
    """SQLite-backed cursor persistence for the connector pipeline.

    Wave 1 ships the seam-and-shape only; methods raise
    :class:`NotImplementedError`. Wave 2 (IM-1) lands the real
    SQLite read / write inside the per-batch transaction.
    """

    # read(source_name) -> Cursor | None
    # Wave 2: SELECT cursor_token FROM connector_cursors WHERE
    # source_name = ?. ``None`` means "first sync - start from the
    # beginning of the source's change stream".
    def read(self, source_name: str) -> Cursor | None:
        raise NotImplementedError("CursorStore.read - Wave 2 (SC-1 ships the seam only).")

    # write(source_name, token) -> None
    # Wave 2: UPSERT INTO connector_cursors. Called from inside the
    # per-batch SQLite transaction so cursor advance and chunk writes
    # commit atomically.
    def write(self, source_name: str, token: Cursor) -> None:
        raise NotImplementedError("CursorStore.write - Wave 2 (SC-1 ships the seam only).")
