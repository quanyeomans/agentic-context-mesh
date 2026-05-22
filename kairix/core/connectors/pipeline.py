"""ConnectorPipeline — the per-batch orchestrator that composes the connector framework.

One canonical pipeline. Connectors and extractors plug in via their
respective Protocols (:class:`~kairix.core.protocols.SourceConnector`,
:class:`~kairix.core.protocols.Extractor`); the pipeline itself knows
nothing about specific sources or formats.

Pipeline body (spec doc §4):

  for change in connector.list_changes(cursor):
      raw = connector.fetch(change.item_id)
      ref = bronze.write(connector.name, change.item_id, raw.raw, raw.mime)
      doc = extractor.extract(raw.raw, raw.mime)
      silver_out = silver.process(
          ref, doc,
          source_uri=connector.source_link(change.item_id),
          source_modified_at=change.modified_at,
          sensitivity=connector.sensitivity_for(change.item_id),
      )
      documents_writer.upsert(silver_out.chunks)
      entity_graph_sink.stage(silver_out.entity_signals)
  cursor_store.write(connector.name, <latest cursor token>)
  db.commit()

All of the above runs inside ONE SQLite transaction per batch. On
silver / writer / sink failure the transaction rolls back and the
cursor stays where it was; the batch is retried on the next worker
tick. Per-item failures (the connector raised on ``fetch`` for one
item) land the item in dead_letter and the loop continues — sibling
items succeed, the cursor advances. After ``failure_count >=
threshold`` the item is considered poisoned and the cursor advances
past it.

Three failure modes map to three behaviours (spec doc §4):

* **Fetch / extract failure on one item** — recorded in
  :class:`~kairix.core.connectors.dead_letter.DeadLetterStore`,
  sibling items proceed, cursor advances at batch end.
* **Persistent fetch failure (`>= threshold` retries)** — item is
  poisoned, stays in dead-letter, cursor advances past it so the
  worker doesn't spin on the same broken record forever.
* **Silver / writer / sink failure (batch-level)** — whole-batch
  rollback, cursor unchanged, retried on next tick.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from kairix.core.connectors.cursor_store import CursorStore
from kairix.core.connectors.dead_letter import DeadLetterStore
from kairix.core.protocols import (
    BronzeStore,
    Chunk,
    EntityGraphSink,
    Extractor,
    SilverProcessor,
    SourceConnector,
)

# Per-item outcome tags returned by ``_process_item`` and consumed by
# ``_process_batch`` to bump the right :class:`BatchResult` counter.
_OUTCOME_PROCESSED = "processed"
_OUTCOME_DEAD_LETTERED = "dead_lettered"


@runtime_checkable
class ChunkWriter(Protocol):
    """Where :class:`~kairix.core.protocols.Chunk` records land.

    The connector pipeline composes a :class:`ChunkWriter` so the
    Silver output can be persisted to the retrieval index without
    Silver itself knowing how the index is shaped. Production wires a
    SQLite-backed writer (``documents`` table + FTS5 reindex);
    integration tests use a capture-only fake.
    """

    def upsert(self, chunks: Sequence[Chunk]) -> int:
        """Persist ``chunks``; return the count successfully written.

        Must NOT commit — the caller's per-batch transaction owns the
        commit so chunk writes, cursor advance, and Bronze writes
        commit together or roll back together.
        """


@dataclass(frozen=True)
class BatchResult:
    """Outcome of one :meth:`ConnectorPipeline.run_batch` call.

    Frozen per F42. ``processed`` is the count of items that flowed
    end-to-end through Bronze + Silver + writer + sink. ``dead_lettered``
    counts items that failed at fetch/extract and landed in
    dead_letter on this batch (excludes pre-existing dead-letter
    entries that incremented). ``poisoned_skipped`` counts items that
    were already at ``failure_count >= threshold`` at batch start and
    were skipped past.
    """

    processed: int
    dead_lettered: int
    poisoned_skipped: int


class ConnectorPipeline:
    """Production connector orchestrator.

    Composes :class:`BronzeStore`, :class:`SilverProcessor`,
    :class:`ChunkWriter`, :class:`EntityGraphSink`,
    :class:`CursorStore`, :class:`DeadLetterStore` around a shared
    :class:`sqlite3.Connection`. The single connection is the
    transaction boundary — every Bronze / cursor / dead-letter /
    chunk write happens against the same connection so the per-batch
    commit / rollback is atomic across all stores.
    """

    def __init__(
        self,
        *,
        db: sqlite3.Connection,
        bronze: BronzeStore,
        silver: SilverProcessor,
        chunk_writer: ChunkWriter,
        entity_graph_sink: EntityGraphSink,
        cursor_store: CursorStore,
        dead_letter: DeadLetterStore,
        dead_letter_threshold: int = 3,
    ) -> None:
        self._db = db
        self._bronze = bronze
        self._silver = silver
        self._chunk_writer = chunk_writer
        self._entity_graph_sink = entity_graph_sink
        self._cursor_store = cursor_store
        self._dead_letter = dead_letter
        self._dead_letter_threshold = dead_letter_threshold

    def run_batch(self, connector: SourceConnector, extractor: Extractor) -> BatchResult:
        """Drive one batch of changes through the per-batch transaction.

        See module docstring for the canonical sequence. Returns a
        :class:`BatchResult` carrying the counts. Re-raises any
        batch-level failure (silver / writer / sink) AFTER rolling
        back the transaction so the worker can log it; per-item
        failures are absorbed into dead_letter and do not propagate.
        """
        cursor = self._cursor_store.read(connector.name)
        try:
            return self._process_batch(connector, extractor, cursor)
        except Exception:
            self._db.rollback()
            raise

    def _process_batch(
        self,
        connector: SourceConnector,
        extractor: Extractor,
        cursor: str | None,
    ) -> BatchResult:
        """Inner per-batch loop, executed inside the SQLite transaction."""
        processed = 0
        dead_lettered = 0
        poisoned_skipped = 0
        latest_modified_at: str | None = None

        for change in connector.list_changes(cursor):
            latest_modified_at = change.modified_at
            if self._dead_letter.is_poisoned(connector.name, change.item_id, threshold=self._dead_letter_threshold):
                poisoned_skipped += 1
                continue
            item_outcome = self._process_item(connector, extractor, change)
            if item_outcome == _OUTCOME_PROCESSED:
                processed += 1
            elif item_outcome == _OUTCOME_DEAD_LETTERED:
                dead_lettered += 1

        # Advance the cursor to the latest observed modified_at (the
        # connector's resumption-token convention). If no changes were
        # seen this batch, leave the cursor unchanged.
        if latest_modified_at is not None:
            self._cursor_store.write(connector.name, latest_modified_at)
        self._db.commit()
        return BatchResult(
            processed=processed,
            dead_lettered=dead_lettered,
            poisoned_skipped=poisoned_skipped,
        )

    def _process_item(
        self,
        connector: SourceConnector,
        extractor: Extractor,
        change: object,
    ) -> str:
        """Process one :class:`ChangeEvent`; return ``"processed"`` or ``"dead_lettered"``.

        Per-item failures (fetch / extract raised) are recorded in
        dead_letter and absorbed — sibling items still process. Silver
        / writer / sink failures are not absorbed: they propagate to
        :meth:`run_batch`'s try/except which rolls back the entire
        batch.
        """
        # Type narrowing — ``change`` is a ChangeEvent at the call-site;
        # we accept ``object`` here to keep the helper signature stable
        # across future ChangeEvent extensions.
        item_id = change.item_id  # type: ignore[attr-defined]  # F3-rationale: change is ChangeEvent from connector.list_changes; attr is on the dataclass.
        modified_at = change.modified_at  # type: ignore[attr-defined]  # F3-rationale: same as item_id above.
        try:
            raw = connector.fetch(item_id)
        except Exception as exc:
            self._dead_letter.record(connector.name, item_id, f"fetch: {exc}")
            return _OUTCOME_DEAD_LETTERED
        try:
            ref = self._bronze.write(connector.name, item_id, raw.raw, raw.mime)
            doc = extractor.extract(raw.raw, raw.mime)
        except Exception as exc:
            self._dead_letter.record(connector.name, item_id, f"extract: {exc}")
            return _OUTCOME_DEAD_LETTERED
        # Silver / writer / sink failures propagate — the batch rolls back.
        silver_out = self._silver.process(
            ref,
            doc,
            source_uri=connector.source_link(item_id),
            source_modified_at=modified_at,
            sensitivity=connector.sensitivity_for(item_id),
        )
        self._chunk_writer.upsert(silver_out.chunks)
        self._entity_graph_sink.stage(silver_out.entity_signals)
        return _OUTCOME_PROCESSED
