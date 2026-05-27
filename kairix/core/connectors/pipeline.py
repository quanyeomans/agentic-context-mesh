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

import logging
import shutil
import sqlite3
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
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

logger = logging.getLogger(__name__)

# Per-item outcome tags returned by ``_process_item`` and consumed by
# ``_process_batch`` to bump the right :class:`BatchResult` counter.
_OUTCOME_PROCESSED = "processed"
_OUTCOME_DEAD_LETTERED = "dead_lettered"


@dataclass
class _BatchTotals:
    """Cross-chunk counters for one ``run_batch`` invocation."""

    processed: int = 0
    dead_lettered: int = 0
    poisoned_skipped: int = 0


@dataclass
class _ChunkAccumulator:
    """In-flight counters for the current uncommitted chunk."""

    processed: int = 0
    dead_lettered: int = 0
    latest_modified_at: str | None = None

    @property
    def size(self) -> int:
        return self.processed + self.dead_lettered

    def record(self, outcome: str, modified_at: str) -> None:
        self.latest_modified_at = modified_at
        if outcome == _OUTCOME_PROCESSED:
            self.processed += 1
        elif outcome == _OUTCOME_DEAD_LETTERED:
            self.dead_lettered += 1

    def reset(self) -> None:
        self.processed = 0
        self.dead_lettered = 0
        self.latest_modified_at = None


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

    ADR-020 additions:

    * ``skipped_low_disk`` — True when the tick was skipped because
      :meth:`SourceConnector.disk_watermark_min_free_bytes` was set and
      the resolved free-bytes count fell below it. Cursor + bronze are
      untouched; operator sees a ``watermark_skip`` log line.
    * ``budget_yielded`` — True when the tick reached
      :meth:`SourceConnector.per_tick_max_items` before the source
      drained. The partial cursor is committed; the next tick resumes
      from there. The operator's signal that a backlog is converging
      over many ticks.
    """

    processed: int
    dead_lettered: int
    poisoned_skipped: int
    skipped_low_disk: bool = False
    budget_yielded: bool = False


_BUDGET_EXHAUSTED_SENTINEL = "F66:budget_exhausted"


class _BudgetExhaustedError(Exception):
    """Internal marker raised when the per-tick budget cap is hit.

    Caught by :meth:`ConnectorPipeline._process_batch` so the partial
    chunk is committed (cursor advances) before returning a
    ``budget_yielded=True`` result. The exception is NEVER surfaced to
    callers — it is a control-flow signal local to the pipeline.
    """


def _default_disk_free_resolver() -> int:
    """Default disk-free resolver — queries ``/data`` if present, else returns ``sys.maxsize``.

    Production deploys mount the engagement-scope volume at ``/data``;
    local dev / CI runs without that mount see ``sys.maxsize`` so the
    watermark gate is effectively disabled (any positive watermark <
    maxsize, so the gate never trips). Tests inject a deterministic
    resolver via ``disk_free_resolver=lambda: <byte_count>``.
    """
    data_dir = Path("/data")
    if data_dir.exists():
        return shutil.disk_usage(str(data_dir)).free
    return sys.maxsize


# F66-exempt: orchestrator reads per_tick_max_items from the connector; no budget of its own
class ConnectorPipeline:
    """Production connector orchestrator.

    Composes :class:`BronzeStore`, :class:`SilverProcessor`,
    :class:`ChunkWriter`, :class:`EntityGraphSink`,
    :class:`CursorStore`, :class:`DeadLetterStore` around a shared
    :class:`sqlite3.Connection`. The single connection is the
    transaction boundary — every Bronze / cursor / dead-letter /
    chunk write happens against the same connection so the per-batch
    commit / rollback is atomic across all stores.

    ADR-020 — per-tick budget + disk-watermark gate. Each tick:

    1. Checks the connector's :attr:`SourceConnector.disk_watermark_min_free_bytes`
       against the configured ``disk_free_resolver`` (default queries
       ``/data``). If free bytes < watermark, the tick yields
       immediately with ``BatchResult(skipped_low_disk=True)`` — cursor
       and bronze untouched.
    2. Drains :meth:`SourceConnector.list_changes` up to
       :attr:`SourceConnector.per_tick_max_items` items, commits the
       partial cursor, and returns ``BatchResult(budget_yielded=True)``.
       The next tick resumes from the persisted cursor — many small
       ticks accrete progress so a 100k-item first-sync converges over
       ~50 hours of 15-min ticks instead of one 50-hour single-tick run.
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
        chunk_size: int = 50,
        disk_free_resolver: Callable[[], int] | None = None,
    ) -> None:
        if chunk_size < 1:
            raise ValueError(
                f"chunk_size must be >= 1; got {chunk_size!r}. "
                "fix: pass a positive int; default is 50. "
                "run: KAIRIX_CONNECTOR_CHUNK_SIZE=50 (env override resolved upstream)"
            )
        self._db = db
        self._bronze = bronze
        self._silver = silver
        self._chunk_writer = chunk_writer
        self._entity_graph_sink = entity_graph_sink
        self._cursor_store = cursor_store
        self._dead_letter = dead_letter
        self._dead_letter_threshold = dead_letter_threshold
        self._chunk_size = chunk_size
        # ADR-020 — explicit-None means "use the production resolver";
        # tests inject a deterministic resolver via this kwarg.
        self._disk_free_resolver: Callable[[], int] = (
            disk_free_resolver if disk_free_resolver is not None else _default_disk_free_resolver
        )

    def run_batch(self, connector: SourceConnector, extractor: Extractor) -> BatchResult:
        """Drive one batch of changes through chunked per-commit transactions.

        Pre-#321: the entire batch ran in ONE SQLite transaction; a
        Silver / writer / sink failure mid-batch rolled back every
        bronze_records row written so far, but the on-disk blobs
        already fsynced by ``BronzeStore.write`` stayed put. On a
        6000-item SharePoint backfill, a single failure leaked
        thousands of orphan files.

        Post-#321: the batch commits every ``chunk_size`` items (and
        again at the end if a partial chunk remains). A failure now
        rolls back at most one chunk's worth of bronze_records rows;
        the on-disk orphans from that chunk are reaped by the
        maintenance scheduler's bronze-orphan stage (#318). Previous
        chunks stay committed and the cursor advances per chunk so the
        next batch resumes after the last committed item.

        Per-item failures (fetch / extract raised) are still absorbed
        into dead_letter and do not propagate. Silver / writer / sink
        failures within a chunk still trigger a rollback — but only of
        that chunk, not the whole batch.

        ADR-020 — before the cursor read, the watermark gate runs: if
        the connector declares a ``disk_watermark_min_free_bytes`` and
        the resolved free-bytes count falls below it, the tick yields
        immediately with ``BatchResult(skipped_low_disk=True)``. The
        cursor row is untouched; the next tick re-checks.
        """
        watermark = connector.disk_watermark_min_free_bytes
        if watermark is not None:
            free_bytes = self._disk_free_resolver()
            if free_bytes < watermark:
                logger.info(
                    "watermark_skip name=%s free=%d min=%d",
                    connector.name,
                    free_bytes,
                    watermark,
                )
                return BatchResult(
                    processed=0,
                    dead_lettered=0,
                    poisoned_skipped=0,
                    skipped_low_disk=True,
                )
        cursor = self._cursor_store.read(connector.name)
        return self._process_batch(connector, extractor, cursor)

    def _process_batch(
        self,
        connector: SourceConnector,
        extractor: Extractor,
        cursor: str | None,
    ) -> BatchResult:
        """Iterate changes and commit every ``chunk_size`` items.

        Returns the cumulative BatchResult across all chunks. Re-raises
        the first chunk-level exception AFTER rolling back the failing
        chunk (so earlier-committed chunks survive) so the worker can
        log it.

        After ``list_changes`` drains successfully, always commit the
        terminal chunk so the connector-supplied ``next_cursor()`` is
        persisted — even on a zero-event tick where the connector
        advanced its server-side delta cursor without surfacing items.
        Skipping the terminal commit on quiet ticks is the bug that
        forced full Graph resync every 15 min (deltaLink clobber, see
        :meth:`SourceConnector.next_cursor` docstring).

        ADR-020 — the per-tick budget cap. After ``per_tick_max_items``
        events have been processed (any outcome), the inner loop raises
        :class:`_BudgetExhaustedError`. The handler commits the partial
        chunk (cursor advances), then returns ``BatchResult(budget_yielded=True)``.
        Many small ticks accrete progress so a 100k-item first-sync
        converges without a single 50-hour tick.
        """
        totals = _BatchTotals()
        chunk = _ChunkAccumulator()
        budget_yielded = False
        budget = connector.per_tick_max_items
        items_seen = 0
        try:
            for change in connector.list_changes(cursor):
                self._process_change(connector, extractor, change, totals, chunk)
                items_seen += 1
                if items_seen >= budget:
                    raise _BudgetExhaustedError(_BUDGET_EXHAUSTED_SENTINEL)
        except _BudgetExhaustedError:
            # Commit the partial chunk so the cursor advances; the next
            # tick resumes from here. NOT a rollback — the work done
            # in this tick is real and must be persisted.
            budget_yielded = True
            logger.info(
                "tick_yielded_at_budget name=%s items=%d",
                connector.name,
                items_seen,
            )
            self._commit_and_flush(connector, totals, chunk)
        except Exception:
            # Roll back the failing partial chunk only; earlier chunks
            # are already committed and their bronze rows + cursor
            # advance survive.
            self._db.rollback()
            raise
        else:
            # Clean drain — always flush after a clean drain so the
            # connector's next_cursor() persists even on a zero-event
            # tick where the server-side delta cursor moved forward.
            self._commit_and_flush(connector, totals, chunk)
        return BatchResult(
            processed=totals.processed,
            dead_lettered=totals.dead_lettered,
            poisoned_skipped=totals.poisoned_skipped,
            budget_yielded=budget_yielded,
        )

    def _process_change(
        self,
        connector: SourceConnector,
        extractor: Extractor,
        change: object,
        totals: _BatchTotals,
        chunk: _ChunkAccumulator,
    ) -> None:
        """One change → poison-check, then process, then maybe-flush the chunk."""
        item_id = change.item_id  # type: ignore[attr-defined]  # F3-rationale: ChangeEvent attr from connector.list_changes
        modified_at = change.modified_at  # type: ignore[attr-defined]  # F3-rationale: same as item_id above
        if self._dead_letter.is_poisoned(connector.name, item_id, threshold=self._dead_letter_threshold):
            totals.poisoned_skipped += 1
            return
        outcome = self._process_item(connector, extractor, change)
        chunk.record(outcome, modified_at)
        if chunk.size >= self._chunk_size:
            self._commit_and_flush(connector, totals, chunk)

    def _commit_and_flush(
        self,
        connector: SourceConnector,
        totals: _BatchTotals,
        chunk: _ChunkAccumulator,
    ) -> None:
        """Cursor-advance + commit; fold chunk counts into totals; reset chunk.

        Writes the connector-supplied ``next_cursor()`` token (NOT the
        per-item ``modified_at`` — that breaks connectors whose cursor
        is an opaque API continuation token). When ``next_cursor()``
        returns ``None`` the cursor write is skipped so a previously-
        persisted cursor isn't clobbered with ``None``. The chunk-level
        accumulators are always reset and folded into totals so the
        in-flight state stays consistent across ticks.
        """
        next_cursor_token = connector.next_cursor()
        if next_cursor_token is not None:
            self._cursor_store.write(connector.name, next_cursor_token)
        self._db.commit()
        totals.processed += chunk.processed
        totals.dead_lettered += chunk.dead_lettered
        chunk.reset()

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
