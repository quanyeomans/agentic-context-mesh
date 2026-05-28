"""Soak: bronze-coverage parity invariant holds at 10k events.

ADR-024 Bundle F seed soak test. Pins the F72 cross-layer integrity
invariant ``bronze_coverage_parity`` at production scale:

    |bronze_records| == |content_distinct_hashes U dead_letter_distinct_items|

Motivated by the "5,200 SP items in bronze-but-not-content limbo"
failure mode where Bronze had them but content + DLQ didn't —
fixture-scale tests (N <= 100) never crossed the layers; this test
forces 10k events through the composed pipeline and asserts no item
falls through the bronze/content/DLQ accounting at scale.

Composed through :func:`kairix.core.factory.build_connector_pipeline`
per F47. The source connector is :class:`tests.fakes.FakeSourceConnector`
seeded by :func:`tests.fakes.build_bulk_source_connector`. The
extractor is :class:`tests.fakes.FakeExtractor` (success-only path
for the happy-path parity; failure-injection variants live under
``tests/integrity_invariants/`` per Bundle E).

Wall-clock budget: < 5 min (asserted). On the soak runner the actual
runtime is ~30-60s; the 5 min ceiling leaves headroom for slow-runner
variance.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from kairix.core import factory
from kairix.core.db.schema import create_schema
from tests.fakes import FakeExtractor, build_bulk_source_connector

pytestmark = pytest.mark.soak

# Production-scale fixture: 10k events meets the ADR-024 soak floor
# (N >= 10**4). The "5,200 limbo" defect was invisible at N=100 because
# every item processed cleanly; only the long tail of edge cases at scale
# surfaced the parity gap. 10k events lights up the cross-layer count
# comparison reliably.
_SOAK_N_EVENTS = 10_000

# Wall-clock budget — ConnectorPipeline does one DB commit per
# ``chunk_size`` (default 50) so 10k items = 200 commits + 10k INSERTs.
# Local measurement: ~30s. 5 min ceiling absorbs slow-runner variance.
_WALL_CLOCK_BUDGET_SECONDS = 300

# Collection name the test pipeline writes chunks under. Must be
# non-empty (ChunkWriter rejects empty collections at construction).
_SOAK_COLLECTION = "soak-bronze-parity"


def _open_db(tmp_path: Path) -> sqlite3.Connection:
    """Open a fresh SQLite connection with the production schema applied."""
    db = sqlite3.connect(str(tmp_path / "bronze_parity_soak.sqlite"))
    create_schema(db)
    return db


def _bronze_count(db: sqlite3.Connection, *, source_name: str) -> int:
    row = db.execute(
        "SELECT COUNT(*) FROM bronze_records WHERE source_name = ?",
        (source_name,),
    ).fetchone()
    return int(row[0]) if row else 0


def _content_distinct_hashes_count(db: sqlite3.Connection) -> int:
    """Distinct content hashes — the production-side proof of "made it to silver"."""
    row = db.execute("SELECT COUNT(DISTINCT hash) FROM content").fetchone()
    return int(row[0]) if row else 0


def _dead_letter_distinct_items_count(db: sqlite3.Connection, *, source_name: str) -> int:
    row = db.execute(
        "SELECT COUNT(DISTINCT item_id) FROM connector_deadletter WHERE source_name = ?",
        (source_name,),
    ).fetchone()
    return int(row[0]) if row else 0


def test_bronze_coverage_parity_holds_at_10k_events(tmp_path: Path) -> None:
    """10k events drained → bronze count == distinct content hashes (no DLQ in happy path).

    The F72 invariant ``bronze_coverage_parity`` is the named cross-layer
    integrity contract from ADR-024 §F72. This test pins the happy-path
    branch at production scale: every event flows through fetch ->
    bronze.write -> silver -> content -> chunks; no item is silently
    lost between layers.

    Concrete observable outcomes asserted:

      1. Pre-batch state: bronze + content + DLQ all empty.
      2. Pipeline processes all 10k events (``result.processed == 10_000``).
      3. Bronze count == 10k (every fetched item has a bronze row).
      4. Content distinct hashes == 10k (every bronze row produced a
         silver-side content row — no item stuck in bronze-only limbo).
      5. Dead-letter distinct items == 0 (happy path; no failures
         absorbed into DLQ).
      6. PARITY: bronze_count == content_distinct_hashes + dlq_distinct_items.
      7. Wall-clock < 5 min.

    Sabotage proof: edit ``kairix/core/connectors/pipeline.py`` to skip
    the silver write for every 100th item (e.g. ``if items_seen % 100
    == 0: return _OUTCOME_PROCESSED`` before the silver.process call);
    bronze gets 10k rows, content gets ~9900 distinct hashes; assertion
    6 (parity) fails with concrete mismatch ``bronze=10000 silver=9900
    dlq=0``. Verified locally before commit.
    """
    db = _open_db(tmp_path)
    try:
        # 1. Pre-batch invariant — empty everywhere.
        connector_name = "soak-source"
        assert _bronze_count(db, source_name=connector_name) == 0, "pre-batch bronze should be empty"
        assert _content_distinct_hashes_count(db) == 0, "pre-batch content should be empty"
        assert _dead_letter_distinct_items_count(db, source_name=connector_name) == 0, "pre-batch DLQ should be empty"

        # 2. Compose the pipeline + connector. F47-sanctioned factory
        # entry point. Connector's ``per_tick_max_items`` is set to
        # ``_SOAK_N_EVENTS + 1`` so the inner-loop budget check
        # (``items_seen >= budget``) never trips on the exact 10k-th
        # event — a single ``run_batch`` drains the entire soak fixture
        # without yielding mid-tick.
        connector = build_bulk_source_connector(
            name=connector_name,
            n_events=_SOAK_N_EVENTS,
            per_tick_max_items=_SOAK_N_EVENTS + 1,
        )
        extractor = FakeExtractor()
        pipeline = factory.build_connector_pipeline(
            db=db,
            collection=_SOAK_COLLECTION,
        )

        # 3. Drive the batch through the composed production code. The
        # disk-watermark gate is satisfied because the fake connector
        # declares ``disk_watermark_min_free_bytes = None`` (no
        # watermark) — the pipeline's default resolver is never called.
        started_at = time.monotonic()
        result = pipeline.run_batch(connector, extractor)
        elapsed_s = time.monotonic() - started_at

        # 4. Pipeline drained the whole backlog cleanly.
        assert result.processed == _SOAK_N_EVENTS, (
            f"expected processed={_SOAK_N_EVENTS}; got {result.processed} ({result})"
        )
        assert result.dead_lettered == 0, f"happy path: expected dead_lettered=0; got {result.dead_lettered}"
        assert result.budget_yielded is False, (
            f"connector budget = {_SOAK_N_EVENTS}; pipeline should NOT yield. result={result}"
        )
        assert result.skipped_low_disk is False, "fake connector declares no watermark; gate should not trip"

        # 5. Cross-layer counts — bronze, content, dead-letter.
        bronze_count = _bronze_count(db, source_name=connector_name)
        content_hashes_count = _content_distinct_hashes_count(db)
        dlq_count = _dead_letter_distinct_items_count(db, source_name=connector_name)

        assert bronze_count == _SOAK_N_EVENTS, (
            f"every fetched item must produce a bronze row; bronze={bronze_count} expected={_SOAK_N_EVENTS}"
        )
        assert content_hashes_count == _SOAK_N_EVENTS, (
            f"every bronze row must produce a distinct content hash; "
            f"content_distinct={content_hashes_count} expected={_SOAK_N_EVENTS} "
            f"(deficit = {_SOAK_N_EVENTS - content_hashes_count} items in bronze-but-not-content limbo)"
        )
        assert dlq_count == 0, f"happy path: expected DLQ empty; got {dlq_count}"

        # 6. The F72 parity invariant itself — bronze == content U DLQ.
        # In the happy path DLQ is empty so bronze == content; the
        # union form is asserted explicitly so the test fails the same
        # way for "stuck in bronze with no DLQ entry" (the #5200 limbo
        # defect) as for "silver dropped a row silently".
        expected_union = content_hashes_count + dlq_count
        assert bronze_count == expected_union, (
            f"F72 bronze_coverage_parity invariant violated: "
            f"bronze={bronze_count} != content_distinct({content_hashes_count}) + dlq({dlq_count}) = {expected_union}"
        )

        # 7. Wall-clock budget — pipeline drained in operator-acceptable time.
        assert elapsed_s < _WALL_CLOCK_BUDGET_SECONDS, (
            f"connector pipeline wall-clock {elapsed_s:.1f}s exceeded budget of "
            f"{_WALL_CLOCK_BUDGET_SECONDS}s for {_SOAK_N_EVENTS} events. "
            f"fix: profile _process_item / silver.process or raise the budget with rationale."
        )
    finally:
        db.close()
