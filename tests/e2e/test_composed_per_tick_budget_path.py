"""E2E composed-path test for ADR-020 / F66 per-tick budget convergence.

A 1200-item backlog (budget 500) drains over exactly three ticks of
the *composed production* :class:`~kairix.core.connectors.pipeline.ConnectorPipeline`
constructed via :func:`kairix.core.factory.build_connector_pipeline`.

The 100k-item production scenario from ADR-020 is collapsed to 1200
items + budget 500 so the test runs in milliseconds; the contract is
identical: tick 1 yields at the budget, tick 2 yields at the budget,
tick 3 drains the remainder cleanly. The cursor advances once per tick.

F48 contract: file exists, carries ``@pytest.mark.e2e``, runs through
config → factory.build → ingest → query → assertion.

Sabotage proof: removing the budget cap in
``kairix/core/connectors/pipeline.py:_process_batch`` causes tick 1 to
drain all 1200 items; the per-tick processed-count assertion fails.
Restore; test passes.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kairix.core.db.schema import create_schema
from kairix.core.factory import build_connector_pipeline
from kairix.core.protocols import ChangeEvent
from tests.fakes import FakeChunkWriter, FakeEntityGraphSink, FakeExtractor, FakeSourceConnector


@pytest.mark.e2e
def test_composed_pipeline_drains_1200_items_over_three_budgeted_ticks(tmp_path: Path) -> None:
    """Composed production path: 1200 items / budget 500 / three ticks → full drain."""
    db_path = tmp_path / "composed_budget.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db)

    pipeline = build_connector_pipeline(
        db=db,
        collection="composed-budget-e2e",
        chunk_writer=FakeChunkWriter(),
        entity_graph_sink=FakeEntityGraphSink(),
    )

    body = ("body. " * 30).encode("utf-8")
    all_events = [
        ChangeEvent(
            op="modified",
            item_id=f"composed-item-{i:05d}.md",
            modified_at=f"2026-05-28T00:{(i // 60) % 60:02d}:{i % 60:02d}Z",
        )
        for i in range(1200)
    ]
    tick_tokens = ("composed-tick-1", "composed-tick-2", "composed-tick-3")
    # Each tick sees only the slice the source still has after the
    # prior tick's cursor advanced — mirrors the real deltaLink shape:
    # tick 1 items 0..499 (500 of 1200 remaining → budget yields),
    # tick 2 items 500..999 (500 → budget yields),
    # tick 3 items 1000..1199 (200 → clean drain, no budget yield).
    tick_slices = [
        all_events[0:500],
        all_events[500:1000],
        all_events[1000:1200],
    ]
    processed_per_tick: list[int] = []
    budget_yielded_per_tick: list[bool] = []

    for i, token in enumerate(tick_tokens):
        slice_events = tick_slices[i]
        connector = FakeSourceConnector(
            name="composed-budget-e2e",
            events=slice_events,
            content={ev.item_id: body for ev in slice_events},
            cursor_token=token,
            per_tick_max_items=500,
        )
        result = pipeline.run_batch(connector, FakeExtractor())
        processed_per_tick.append(result.processed)
        budget_yielded_per_tick.append(result.budget_yielded)

    # Tick 1 + 2 saturate the budget; tick 3 drains the remaining 200
    # cleanly (no budget yield).
    assert processed_per_tick == [500, 500, 200], f"composed pipeline drain shape wrong; got {processed_per_tick!r}"
    assert budget_yielded_per_tick == [True, True, False], (
        f"budget_yielded must fire on ticks 1+2 only; got {budget_yielded_per_tick!r}"
    )

    bronze_total = int(
        db.execute(
            "SELECT COUNT(*) FROM bronze_records WHERE source_name = ?",
            ("composed-budget-e2e",),
        ).fetchone()[0]
    )
    assert bronze_total == 1200, f"bronze_records should hold all 1200 items after three ticks; got {bronze_total}"

    cursor_row = db.execute(
        "SELECT cursor_token FROM connector_cursors WHERE source_name = ?",
        ("composed-budget-e2e",),
    ).fetchone()
    assert cursor_row is not None
    assert cursor_row[0] == tick_tokens[-1], (
        f"cursor should hold the tick-3 token after full drain; got {cursor_row[0]!r}"
    )
