"""DeadLetterStore - records items the connector pipeline could not process.

After the configured retry count, a failed item is recorded in the
``connector_deadletter`` table (see schema in spec doc §7) and the
cursor advances past it so sibling items proceed. Operators inspect
the dead-letter table to triage source-side problems (auth failure,
unreadable bytes, format outside the escalation chain).

Method bodies are intentionally single-statement
``raise NotImplementedError`` calls so the F19 unused-parameter rule
recognises them as abstract-style skeletons.
"""

from __future__ import annotations


class DeadLetterStore:
    """SQLite-backed dead-letter recorder for the connector pipeline.

    Wave 1 ships the seam-and-shape only; :meth:`record` raises
    :class:`NotImplementedError`. Wave 2 (IM-1) lands the real
    SQLite UPSERT inside the per-batch transaction - incrementing
    ``failure_count`` on repeat failures of the same ``item_id``.
    """

    # record(source_name, item_id, error) -> None
    # Wave 2: UPSERT INTO connector_deadletter; on conflict bump
    # ``failure_count`` and refresh ``last_error`` + ``last_attempt``.
    # Called from the per-batch transaction; commits atomically with
    # cursor advance.
    def record(self, source_name: str, item_id: str, error: str) -> None:
        raise NotImplementedError("DeadLetterStore.record - Wave 2 (SC-1 ships the seam only).")
