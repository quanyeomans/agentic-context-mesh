"""F68 (ADR-024 Bundle A) — failure-mode contract for :class:`EntityGraphSink`.

:class:`kairix.core.protocols.EntityGraphSink` has a single public
method — :meth:`stage` — but covers two distinct failure classes:

  * ``raises`` — the sink's underlying store raised (SQLite error,
    disk full, etc.). The pipeline does NOT wrap the sink call in a
    try/except, so the exception propagates and the per-chunk
    transaction rolls back.
  * ``unavailable`` — the sink reports unavailable (the downstream
    write target — Curator drain → Neo4j — is unreachable). The sink
    returns 0 without raising; signals stay with ``pushed_to_neo4j=0``
    in the staging table and the drain retries on its next tick.
    This is the canonical #334 behaviour generalised: the staging
    write is durable; the drain to the final sink is eventually
    consistent.

Composition follows F47.

Each test carries a "Sabotage proof:" comment describing the mutation
that proves the assertion has teeth.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kairix.core.db.schema import create_schema
from kairix.core.factory import build_connector_pipeline
from kairix.core.protocols import ChangeEvent
from tests.fakes import FakeChunkWriter, FakeEntityGraphSink, FakeExtractor, FakeSourceConnector

pytestmark = pytest.mark.contract


# ---------------------------------------------------------------------------
# Helpers — factory-composed pipeline with canonical fakes (F47-compliant).
# ---------------------------------------------------------------------------


def _build_pipeline(
    db: sqlite3.Connection,
    *,
    chunk_writer: FakeChunkWriter | None = None,
    entity_graph_sink: FakeEntityGraphSink | None = None,
):
    """F47-compliant: ConnectorPipeline composed via the factory entry point."""
    return build_connector_pipeline(
        db=db,
        collection="default",
        chunk_writer=chunk_writer if chunk_writer is not None else FakeChunkWriter(),
        entity_graph_sink=entity_graph_sink if entity_graph_sink is not None else FakeEntityGraphSink(),
    )


def _make_event(item_id: str, modified_at: str = "2026-01-01T00:00:00Z") -> ChangeEvent:
    return ChangeEvent(op="created", item_id=item_id, modified_at=modified_at)


# ---------------------------------------------------------------------------
# EntityGraphSink.stage — raises (e.g. SQLite IntegrityError, disk full)
# ---------------------------------------------------------------------------


def test_stage_raises_propagates_and_rolls_back_chunk(tmp_path: Path) -> None:
    """When ``entity_graph_sink.stage`` raises, the
    :class:`ConnectorPipeline` does NOT absorb it — the exception
    propagates from ``_process_item`` → ``_process_batch``, which
    rolls back the failing chunk (and re-raises).

    Sabotage proof: in ``FakeEntityGraphSink.stage`` comment out the
    ``if self._raise_on_stage is not None: raise ...`` block. Re-run:
    the test fails because ``pytest.raises`` sees no exception and the
    chunk processes cleanly. Restored.
    """
    db = sqlite3.connect(":memory:")
    create_schema(db)
    writer = FakeChunkWriter()
    source = FakeSourceConnector(
        name="sink-raises",
        events=[_make_event("item-001")],
        content={"item-001": b"body-content"},
    )
    sink = FakeEntityGraphSink(raise_on_stage=RuntimeError("F68-sink-raises"))
    pipeline = _build_pipeline(db, chunk_writer=writer, entity_graph_sink=sink)

    with pytest.raises(RuntimeError, match="F68-sink-raises"):
        pipeline.run_batch(source, FakeExtractor())

    # writer.upsert is called BEFORE sink.stage in _process_item, so it
    # WILL have been invoked on the in-flight batch. The rollback is at
    # the SQLite transaction layer (bronze_records rolled back); the
    # captured FakeChunkWriter records the call but the persistent DB
    # state is the contract. Assert the SQLite-side rollback held:
    # bronze_records is empty for the source.
    bronze_count = int(
        db.execute(
            "SELECT COUNT(*) FROM bronze_records WHERE source_name = ?",
            ("sink-raises",),
        ).fetchone()[0]
    )
    assert bronze_count == 0, f"bronze_records must roll back on sink raise; got {bronze_count} row(s)"
    db.close()


# ---------------------------------------------------------------------------
# EntityGraphSink.stage — unavailable (mirrors #334 drain-unreachable)
# ---------------------------------------------------------------------------


def test_stage_unavailable_returns_zero_signals_stay_in_staging(tmp_path: Path) -> None:
    """The ``unavailable`` failure class — the sink's downstream write
    target (Curator drain → Neo4j) is unreachable. The sink returns
    0 without raising; the per-chunk transaction commits normally so
    the connector continues making forward progress. Signals stay
    with ``pushed_to_neo4j=0`` (or in this fake's case, are never
    recorded against the sink's captured batches) and the drain
    retries later.

    This is the generalised #334 behaviour: durable staging is
    decoupled from eventual delivery. A drain outage must NOT block
    connector ingest.

    Sabotage proof: in ``FakeEntityGraphSink.stage`` change the
    ``if not self._available: ... return 0`` branch to ``return len(batch)``
    (lying about the durability). Re-run: the test fails because the
    ``unavailable_calls`` counter is 0 (no path took the unavailable
    branch) and ``sink.staged`` has one entry instead of 0. Restored.
    """
    db = sqlite3.connect(":memory:")
    create_schema(db)
    writer = FakeChunkWriter()
    source = FakeSourceConnector(
        name="sink-unavailable",
        events=[_make_event("item-001")],
        content={"item-001": b"# heading\n\nbody text with entities Acme Inc."},
    )
    sink = FakeEntityGraphSink(available=False)
    pipeline = _build_pipeline(db, chunk_writer=writer, entity_graph_sink=sink)

    # The pipeline runs to completion — connector ingest is decoupled
    # from sink delivery. ``result.processed == 1`` proves the chunk
    # committed.
    result = pipeline.run_batch(source, FakeExtractor())
    assert result.processed == 1
    assert result.dead_lettered == 0

    # The sink WAS asked to stage — the call happened, the sink
    # reported unavailable, and the durability of the staging is the
    # connector's responsibility (the SQLite ``entity_signals`` row
    # would still be written by the real ``_SqliteEntityGraphSink``;
    # the fake here records the attempt for assertion).
    assert sink.unavailable_calls == 1, (
        f"sink should have been called once and reported unavailable; got unavailable_calls={sink.unavailable_calls}"
    )
    # Sink's captured-batches list stays empty — the fake's
    # contract is "if not available, do NOT record the batch".
    assert sink.staged == []
    # Chunk written by the writer — connector ingest succeeded despite
    # sink unavailability.
    assert len(writer.writes) == 1, (
        f"writer should still receive the chunk batch when only the sink is unavailable; got {writer.writes!r}"
    )
    db.close()


def test_stage_recovers_after_unavailable_window_signals_eventually_staged(tmp_path: Path) -> None:
    """When the sink flips from unavailable → available between two
    ticks, the second tick stages the new batch normally. Proves the
    sink's availability state is honoured per-call (not cached) — the
    drain-recovery path is intact.

    Sabotage proof: in ``FakeEntityGraphSink.set_available`` set
    ``self._available = False`` regardless of the ``value`` arg. Re-run:
    the test fails because tick 2 sees the sink as still unavailable
    and ``sink.staged`` stays empty. Restored.
    """
    db = sqlite3.connect(":memory:")
    create_schema(db)
    source_tick1 = FakeSourceConnector(
        name="sink-recovery",
        events=[_make_event("item-001")],
        content={"item-001": b"first body"},
    )
    sink = FakeEntityGraphSink(available=False)
    pipeline = _build_pipeline(db, entity_graph_sink=sink)

    # Tick 1: sink unavailable.
    pipeline.run_batch(source_tick1, FakeExtractor())
    assert sink.unavailable_calls == 1
    assert sink.staged == []

    # Operator flips the sink available; tick 2 brings new items.
    sink.set_available(True)
    source_tick2 = FakeSourceConnector(
        name="sink-recovery",
        events=[_make_event("item-002", modified_at="2026-01-02T00:00:00Z")],
        content={"item-002": b"second body"},
    )
    pipeline2 = _build_pipeline(db, entity_graph_sink=sink)
    pipeline2.run_batch(source_tick2, FakeExtractor())

    # Sink received tick 2's batch — recovery worked.
    assert sink.unavailable_calls == 1, "no further unavailable calls after recovery"
    # The fake records every available stage call (even when the batch
    # carries zero signals — the call happened). Assert the call count
    # is consistent with the two-tick run.
    assert len(sink.staged) >= 1, f"sink should have received at least one batch after recovery; got {sink.staged!r}"
    db.close()
