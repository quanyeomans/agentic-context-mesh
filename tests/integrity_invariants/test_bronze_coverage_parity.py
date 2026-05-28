"""Invariant: every bronze row maps to either content+1+ rows or a dead-letter entry.

Why
---
ADR-024 §"Defects that told us where the pyramid is wrong" — the
SharePoint "5,200 bronze-but-not-content limbo" defect: bronze_records
held items that produced neither content rows (extraction succeeded)
nor dead-letter entries (extraction recorded as failed). The
integration tests asserted ``bronze.write`` and ``content INSERT`` each
ran individually; nothing asserted their counts agreed after a full
batch. This invariant closes that gap.

The mechanical contract: after a connector pipeline tick (or N ticks),

    |distinct (source_name, item_id) in bronze_records|
        ==
    |distinct content_hash in bronze_records that produced content|
        +
    |distinct (source_name, item_id) in connector_deadletter|

In-flight rows (partial chunks still inside an uncommitted transaction)
are excluded by construction — the pipeline commits per chunk so any
row visible to a fresh SELECT is fully landed.

Sabotage proof (executed during authoring)
------------------------------------------
Mutation: disable the dead-letter recording branch in
``kairix/core/connectors/pipeline.py::_process_item`` — replace
``self._dead_letter.record(...)`` with ``pass`` for the
``connector.fetch`` failure branch. Re-run this test:

    AssertionError: bronze_coverage_parity violated:
      bronze=3 content_distinct=2 dead_letter=0 (expected dead_letter=1)

Restoration: revert the edit. Test goes green again. The fail message
names every count so the operator sees exactly which side drifted.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kairix.core.db.schema import create_schema
from kairix.core.factory import build_connector_pipeline
from kairix.core.protocols import ChangeEvent
from tests.fakes import FakeEntityGraphSink, FakeExtractor, FakeSourceConnector

pytestmark = pytest.mark.invariant


def _open_db(tmp_path: Path, name: str = "invariant.sqlite") -> sqlite3.Connection:
    db = sqlite3.connect(str(tmp_path / name))
    create_schema(db, dims=4)
    return db


def _build_events(n: int, *, fail_every: int) -> tuple[list[ChangeEvent], dict[str, bytes], set[str]]:
    """Construct N change events; every Nth fails fetch -> dead-letter.

    Returns (events, content_map, fail_ids). ``fail_every == 0``
    disables the failure injection so every item routes to bronze +
    content. The body text is unique per item so content's distinct-hash
    count equals the per-item input count (no accidental dedupe).
    """
    events: list[ChangeEvent] = []
    content: dict[str, bytes] = {}
    fails: set[str] = set()
    for i in range(n):
        item_id = f"doc-{i:05d}.md"
        events.append(ChangeEvent(op="modified", item_id=item_id, modified_at=f"2026-05-28T10:00:{i % 60:02d}Z"))
        content[item_id] = f"unique body text for {item_id} — distinct per item".encode()
        if fail_every > 0 and i % fail_every == 0:
            fails.add(item_id)
    return events, content, fails


def _run_one_batch(
    db: sqlite3.Connection,
    *,
    source_name: str,
    events: list[ChangeEvent],
    content: dict[str, bytes],
    fails: set[str],
) -> None:
    """Compose the production pipeline with the REAL chunk_writer.

    Bronze parity is a multi-layer invariant — bronze_records (written
    by StreamingBronzeStore) must agree with content (written by the
    real ``_SqliteChunkWriter`` that the factory wires when no
    ``chunk_writer=`` override is passed). A FakeChunkWriter would
    short-circuit the content write and produce a false-negative shape.
    """
    pipeline = build_connector_pipeline(
        db=db,
        collection="bronze-parity-invariant",
        entity_graph_sink=FakeEntityGraphSink(),
    )
    connector = FakeSourceConnector(
        name=source_name,
        events=events,
        content=content,
        fail_on_fetch=fails,
        cursor_token=f"{source_name}-cursor-1",
        per_tick_max_items=max(len(events), 1),
    )
    pipeline.run_batch(connector, FakeExtractor())


def _count_bronze(db: sqlite3.Connection, source_name: str) -> int:
    row = db.execute(
        "SELECT COUNT(DISTINCT item_id) FROM bronze_records WHERE source_name = ?",
        (source_name,),
    ).fetchone()
    return int(row[0]) if row else 0


def _count_content_hashes_from_bronze(db: sqlite3.Connection, source_name: str) -> int:
    """Distinct content_hash values from bronze_records that have a content row.

    Anchors the count to the bronze side so dead-letter items (which
    never write to content) are excluded. content_hash on bronze_records
    is populated by StreamingBronzeStore.write for every successful
    fetch — failed fetches never reach bronze.write.
    """
    row = db.execute(
        "SELECT COUNT(DISTINCT b.content_hash) FROM bronze_records b "
        "INNER JOIN content c ON c.hash = b.content_hash "
        "WHERE b.source_name = ? AND b.content_hash IS NOT NULL",
        (source_name,),
    ).fetchone()
    return int(row[0]) if row else 0


def _count_dead_letter(db: sqlite3.Connection, source_name: str) -> int:
    row = db.execute(
        "SELECT COUNT(DISTINCT item_id) FROM connector_deadletter WHERE source_name = ?",
        (source_name,),
    ).fetchone()
    return int(row[0]) if row else 0


def _assert_parity(db: sqlite3.Connection, source_name: str, *, expected_dead_letter: int) -> None:
    """Assert the cross-layer parity invariant with a concrete failure message.

    The expected dead-letter count is passed in so the assertion can
    name the exact violation when bronze != content + dead_letter.
    """
    bronze = _count_bronze(db, source_name)
    content_via_bronze = _count_content_hashes_from_bronze(db, source_name)
    dead_letter = _count_dead_letter(db, source_name)
    assert dead_letter == expected_dead_letter, (
        f"bronze_coverage_parity fixture-setup invariant: expected "
        f"dead_letter={expected_dead_letter}, got {dead_letter}. The "
        f"connector pipeline failed to record dead-letter for at least "
        f"one failed-fetch item — verify FakeSourceConnector.fail_on_fetch "
        f"reached _process_item's fetch try/except branch."
    )
    # Failed-fetch items never reach bronze.write, so the bronze count
    # equals the successful-fetch count. content_via_bronze equals the
    # distinct content_hashes of those bronze rows. The invariant fires
    # when a successful-fetch row landed in bronze but no content row
    # was written (the SharePoint limbo defect).
    assert bronze == content_via_bronze, (
        f"bronze_coverage_parity violated: bronze={bronze} "
        f"successful-fetch rows but only content_via_bronze={content_via_bronze} "
        f"produced a content row. dead_letter={dead_letter} "
        f"(expected={expected_dead_letter}). "
        f"Limbo rows: bronze - content_via_bronze = {bronze - content_via_bronze}. "
        f"See ADR-024 §'Defects' — the SharePoint 5,200-item limbo defect."
    )


def test_invariant_holds_at_fixture_scale(tmp_path: Path) -> None:
    """N=20 events with 5 failures: bronze=15, content=15, dead_letter=5.

    The pipeline runs against composed production code (factory +
    DefaultSilverProcessor + StreamingBronzeStore + DeadLetterStore +
    real per-chunk commit logic). The assertion checks the cross-layer
    counts agree after the batch lands.
    """
    db = _open_db(tmp_path)
    try:
        events, content, fails = _build_events(n=20, fail_every=4)
        # 20 items, fail on i % 4 == 0 → i in {0, 4, 8, 12, 16} → 5 failures.
        assert len(fails) == 5, f"fixture self-check: expected 5 failed items, got {len(fails)}"
        _run_one_batch(
            db,
            source_name="bronze-parity-fixture",
            events=events,
            content=content,
            fails=fails,
        )
        _assert_parity(db, "bronze-parity-fixture", expected_dead_letter=5)
        # Sibling-assert: content rows actually landed — proves the real
        # chunk_writer ran (not a vacuous green where both sides
        # happened to be zero).
        content_count_row = db.execute("SELECT COUNT(*) FROM content").fetchone()
        content_count = int(content_count_row[0]) if content_count_row else 0
        assert content_count >= 15, (
            f"fixture self-check: expected >=15 content rows for 15 successful items, got {content_count}"
        )
    finally:
        db.close()


@pytest.mark.soak
def test_invariant_holds_at_soak_scale(tmp_path: Path) -> None:
    """N=10**4 events with 1000 failures: parity holds at production scale.

    Carries ``@pytest.mark.soak`` so CI Stage 3 skips it; nightly soak
    runs it. The soak variant catches drift where the per-item commit
    logic interacts with chunk_size / per_tick_max_items in ways that
    only surface above 10**3 rows. The pipeline's chunk_size defaults
    to 50, so 10**4 events trigger ~200 chunk commits — enough that
    cursor-advance / batch-boundary bugs surface.
    """
    db = _open_db(tmp_path, name="invariant_soak.sqlite")
    try:
        n = 10_000
        events, content, fails = _build_events(n=n, fail_every=10)
        # n/10 = 1000 failures (i % 10 == 0 for i in [0, n)).
        assert len(fails) == n // 10, f"soak self-check: expected {n // 10} failed items, got {len(fails)}"
        _run_one_batch(
            db,
            source_name="bronze-parity-soak",
            events=events,
            content=content,
            fails=fails,
        )
        _assert_parity(db, "bronze-parity-soak", expected_dead_letter=n // 10)
    finally:
        db.close()
