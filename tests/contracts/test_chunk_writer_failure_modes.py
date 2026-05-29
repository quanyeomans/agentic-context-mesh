"""F68 (ADR-024 Bundle A) — failure-mode contract for :class:`ChunkWriter`.

:class:`kairix.core.protocols.ChunkWriter` has a single public method —
:meth:`upsert` — and the operational failure class that bites in
production is ``raises``: the writer's underlying store (SQLite
``IntegrityError``, ``DiskFull``, ``Locked``, a failing FTS5 rebuild)
raises on a write. The pipeline does NOT wrap the writer call in a
try/except — the exception propagates from ``_process_item`` →
``_process_batch``, which rolls back the per-batch transaction so
chunks, cursor advance, and Bronze writes all commit-or-rollback
together.

Composition follows F47.

Each test carries a "Sabotage proof:" comment describing the mutation
that proves the assertion has teeth.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from kairix.core.db.schema import create_schema
from kairix.core.factory import build_connector_pipeline
from kairix.core.protocols import ChangeEvent
from tests.fakes import FakeEntityGraphSink, FakeExtractor, FakeSourceConnector

pytestmark = pytest.mark.contract


@dataclass
class _RaisingChunkWriter:
    """ChunkWriter that raises ``exc`` on every upsert call.

    Mirrors the inline ``_RaisingChunkWriter`` from
    ``tests/integration/test_connector_pipeline_failure_injection.py``
    but keyed on a single configurable exception (not a call-count
    threshold) — the contract being proven here is "any writer raise
    propagates and rolls back," not "the Nth raise dead-letters".
    """

    exc: Exception = field(default_factory=lambda: RuntimeError("F68-writer-raises"))
    calls: int = 0

    def upsert(self, chunks: Any) -> int:
        self.calls += 1
        raise self.exc


def _build_pipeline(
    db: sqlite3.Connection,
    *,
    chunk_writer: _RaisingChunkWriter,
    entity_graph_sink: FakeEntityGraphSink | None = None,
):
    """F47-compliant: ConnectorPipeline composed via the factory entry point."""
    return build_connector_pipeline(
        db=db,
        collection="default",
        chunk_writer=chunk_writer,
        entity_graph_sink=entity_graph_sink if entity_graph_sink is not None else FakeEntityGraphSink(),
    )


def _make_event(item_id: str, modified_at: str = "2026-01-01T00:00:00Z") -> ChangeEvent:
    return ChangeEvent(op="created", item_id=item_id, modified_at=modified_at)


# ---------------------------------------------------------------------------
# ChunkWriter.upsert — raises (e.g. SQLite IntegrityError, FTS5 rebuild error)
# ---------------------------------------------------------------------------


def test_upsert_raises_propagates_and_rolls_back_bronze(tmp_path: Path) -> None:
    """When ``chunk_writer.upsert`` raises, the
    :class:`ConnectorPipeline` does NOT absorb it — the exception
    propagates from ``_process_item`` → ``_process_batch``, which
    rolls back the per-batch transaction (Bronze write included).

    Sabotage proof: in ``ConnectorPipeline._process_item`` wrap the
    ``self._chunk_writer.upsert(...)`` call in
    ``try: ... except Exception: pass``. Re-run: the test fails because
    ``pytest.raises`` sees no exception AND ``bronze_records`` retains
    the in-flight row instead of being rolled back. Restored.
    """
    db = sqlite3.connect(":memory:")
    create_schema(db)
    writer = _RaisingChunkWriter()
    source = FakeSourceConnector(
        name="writer-raises",
        events=[_make_event("item-001")],
        content={"item-001": b"body-content"},
    )
    pipeline = _build_pipeline(db, chunk_writer=writer)

    with pytest.raises(RuntimeError, match="F68-writer-raises"):
        pipeline.run_batch(source, FakeExtractor())

    # The writer was actually called — the exception happened at the
    # writer boundary, not before it.
    assert writer.calls == 1, f"writer should have been called exactly once; got calls={writer.calls}"

    # Per-batch transaction rolled back: bronze_records must be empty
    # for this source. The SQLite-side rollback is the durable contract.
    bronze_count = int(
        db.execute(
            "SELECT COUNT(*) FROM bronze_records WHERE source_name = ?",
            ("writer-raises",),
        ).fetchone()[0]
    )
    assert bronze_count == 0, f"bronze_records must roll back on writer raise; got {bronze_count} row(s)"
    db.close()
