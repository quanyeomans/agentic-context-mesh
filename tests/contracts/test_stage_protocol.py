"""Contract + unit tests for the Stage Protocol + StageRunner variants (ADR-026 Track A main).

Three contracts to prove:

1. **Stage Protocol shape** — a class with ``name``, ``process``, and
   ``classify_exception`` satisfies :class:`Stage` via the
   ``isinstance`` runtime check.
2. **IsolatedStageRunner** — absorbs every exception into an outcome,
   never raises, threads the classified code through.
3. **BatchTransactionStageRunner.run_per_item** — catches, records to
   dead_letter, returns the outcome with ``dead_lettered=True``.
4. **BatchTransactionStageRunner.run_batch_critical** — emits then
   re-raises so the caller can roll back the per-batch transaction.

Each test has a "Sabotage proof:" comment describing the mutation
that proves it has teeth.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import pytest

from kairix.core.connectors.dead_letter import DeadLetterStore
from kairix.core.db.schema import create_schema
from kairix.core.observability.stage import (
    BatchTransactionStageRunner,
    IsolatedStageRunner,
    Stage,
    StageOutcome,
)
from kairix.core.observability.stage_contexts import FetchContext, StageContext
from kairix.core.observability.status_codes import Severity, StatusCode

pytestmark = pytest.mark.contract


# ---------------------------------------------------------------------------
# Test stages — minimal Stage Protocol implementations.
# ---------------------------------------------------------------------------


@dataclass
class _OkStage:
    """Stage that always returns FETCH_OK."""

    name: str = "fetch"

    def process(self, ctx: StageContext) -> StageOutcome:
        return StageOutcome(code=StatusCode.FETCH_OK, output=b"body-bytes")

    def classify_exception(self, exc: BaseException) -> StatusCode:
        return StatusCode.PIPELINE_STAGE_NO_EMIT


@dataclass
class _RaisingStage:
    """Stage that always raises; classifies to FETCH_TIMEOUT."""

    name: str = "fetch"
    exc_class: type[BaseException] = RuntimeError

    def process(self, ctx: StageContext) -> StageOutcome:
        raise self.exc_class("synthetic-failure")

    def classify_exception(self, exc: BaseException) -> StatusCode:
        return StatusCode.FETCH_TIMEOUT


@dataclass
class _WarnStage:
    """Stage that returns a WARN-severity code."""

    name: str = "fetch"

    def process(self, ctx: StageContext) -> StageOutcome:
        return StageOutcome(code=StatusCode.FETCH_THROTTLED, output=None, detail={"retry_after": 30})

    def classify_exception(self, exc: BaseException) -> StatusCode:
        return StatusCode.PIPELINE_STAGE_NO_EMIT


# ---------------------------------------------------------------------------
# Stage Protocol — runtime shape check
# ---------------------------------------------------------------------------


def test_stage_protocol_runtime_check_passes_for_minimal_impl() -> None:
    """Any class with ``name``, ``process``, ``classify_exception`` is a Stage.

    Sabotage proof: rename ``process`` to ``do_work`` on ``_OkStage``;
    the isinstance check fails.
    """
    stage = _OkStage()
    assert isinstance(stage, Stage)


# ---------------------------------------------------------------------------
# IsolatedStageRunner — never raises
# ---------------------------------------------------------------------------


def test_isolated_runner_passes_ok_outcome_through() -> None:
    """When the stage returns OK, the runner returns the same outcome.

    Sabotage proof: change ``_OkStage.process`` to ``return StageOutcome(code=FETCH_TIMEOUT, ...)``;
    the assertion ``outcome.code == FETCH_OK`` fails.
    """
    runner = IsolatedStageRunner(_OkStage(), db=None)
    ctx = FetchContext(source_name="src", item_id="item-001")
    outcome = runner.run(ctx)
    assert outcome.code == StatusCode.FETCH_OK
    assert outcome.output == b"body-bytes"


def test_isolated_runner_absorbs_exception_into_classified_outcome() -> None:
    """When the stage raises, the runner returns an outcome with the
    classified code; no exception escapes.

    Sabotage proof: in :class:`IsolatedStageRunner.run` change
    ``return StageOutcome(...)`` in the except branch to ``raise``;
    the test fails because ``pytest.raises`` would be required.
    """
    runner = IsolatedStageRunner(_RaisingStage(), db=None)
    ctx = FetchContext(source_name="src", item_id="item-001")
    outcome = runner.run(ctx)
    # Did NOT raise — outcome carries the classified code.
    assert outcome.code == StatusCode.FETCH_TIMEOUT
    assert outcome.code.severity == Severity.ERROR
    assert outcome.detail["exception_class"] == "RuntimeError"
    assert "synthetic-failure" in outcome.detail["exception_message"]


def test_isolated_runner_with_db_writes_timeline_row() -> None:
    """With db wired, the runner's emit writes a row to ``pipeline_item_status``.

    Sabotage proof: comment out the ``_emit_outcome(emit, outcome.code, ...)``
    call in :class:`IsolatedStageRunner.run`'s success branch; the
    timeline row count drops to zero (or just the NO_EMIT fail-safe).
    """
    db = sqlite3.connect(":memory:")
    create_schema(db)
    runner = IsolatedStageRunner(_OkStage(), db=db)
    ctx = FetchContext(source_name="src", item_id="item-001")
    runner.run(ctx)
    rows = db.execute(
        "SELECT status_code, severity FROM pipeline_item_status WHERE source_name=? AND item_id=?",
        ("src", "item-001"),
    ).fetchall()
    assert len(rows) == 1, f"expected one timeline row; got {rows!r}"
    assert rows[0][0] == "FETCH_OK"
    assert rows[0][1] == "ok"
    db.close()


# ---------------------------------------------------------------------------
# BatchTransactionStageRunner.run_per_item — catches + dead-letters
# ---------------------------------------------------------------------------


def test_batch_runner_per_item_records_dead_letter_on_exception() -> None:
    """On exception, run_per_item records to dead_letter, returns outcome,
    and does NOT raise.

    Sabotage proof: comment out the ``self._dead_letter.record(...)``
    call; the dead_letter row count stays zero and the assertion
    fails.
    """
    db = sqlite3.connect(":memory:")
    create_schema(db)
    dead_letter = DeadLetterStore(db)
    runner = BatchTransactionStageRunner(_RaisingStage(), db=db, dead_letter=dead_letter)
    ctx = FetchContext(source_name="src", item_id="item-001")
    outcome = runner.run_per_item(ctx)
    # No raise — outcome carries the classified code.
    assert outcome.code == StatusCode.FETCH_TIMEOUT
    assert outcome.detail["dead_lettered"] is True
    rows = db.execute(
        "SELECT item_id, last_error FROM connector_deadletter WHERE source_name=?",
        ("src",),
    ).fetchall()
    assert len(rows) == 1, f"expected one dead_letter row; got {rows!r}"
    assert rows[0][0] == "item-001"
    assert "fetch" in rows[0][1].lower()
    db.close()


def test_batch_runner_per_item_without_dead_letter_raises_value_error() -> None:
    """run_per_item without dead_letter wired raises eagerly — fails
    loud at the call site rather than silently dropping records.

    Sabotage proof: remove the ``if self._dead_letter is None: raise``
    guard; the test fails because no ValueError is raised and the
    sentinel-flag detection downstream gets bypassed.
    """
    runner = BatchTransactionStageRunner(_RaisingStage(), db=None, dead_letter=None)
    ctx = FetchContext(source_name="src", item_id="item-001")
    with pytest.raises(ValueError, match="dead_letter"):
        runner.run_per_item(ctx)


def test_batch_runner_per_item_passes_ok_outcome_through() -> None:
    """When the stage returns OK, run_per_item doesn't touch dead_letter.

    Sabotage proof: make run_per_item always call dead_letter.record;
    the dead_letter row count goes from 0 → 1 and the assertion fails.
    """
    db = sqlite3.connect(":memory:")
    create_schema(db)
    dead_letter = DeadLetterStore(db)
    runner = BatchTransactionStageRunner(_OkStage(), db=db, dead_letter=dead_letter)
    ctx = FetchContext(source_name="src", item_id="item-001")
    outcome = runner.run_per_item(ctx)
    assert outcome.code == StatusCode.FETCH_OK
    rows = db.execute("SELECT COUNT(*) FROM connector_deadletter WHERE source_name=?", ("src",)).fetchone()
    assert rows[0] == 0, "OK outcome must not write to dead_letter"
    db.close()


# ---------------------------------------------------------------------------
# BatchTransactionStageRunner.run_batch_critical — emits + re-raises
# ---------------------------------------------------------------------------


def test_batch_runner_critical_propagates_exception_after_emit() -> None:
    """On exception, run_batch_critical emits the classified code AND
    re-raises so the caller can roll back.

    Sabotage proof: change the ``raise`` in
    ``BatchTransactionStageRunner.run_batch_critical``'s except branch
    to ``return``; the test fails because ``pytest.raises`` sees no
    exception, and the batch-rollback contract is broken.
    """
    db = sqlite3.connect(":memory:")
    create_schema(db)
    runner = BatchTransactionStageRunner(_RaisingStage(name="silver"), db=db)
    ctx = FetchContext(source_name="src", item_id="item-001")
    with pytest.raises(RuntimeError, match="synthetic-failure"):
        runner.run_batch_critical(ctx)
    # Emit DID happen before the re-raise — timeline row exists.
    rows = db.execute(
        "SELECT status_code, severity FROM pipeline_item_status WHERE source_name=? AND stage=?",
        ("src", "silver"),
    ).fetchall()
    assert len(rows) == 1, f"expected one timeline row; got {rows!r}"
    assert rows[0][0] == "FETCH_TIMEOUT"  # _RaisingStage classifies to FETCH_TIMEOUT
    assert rows[0][1] == "error"
    db.close()


def test_batch_runner_critical_returns_ok_outcome_without_raising() -> None:
    """On OK outcome, run_batch_critical returns it without raising.

    Sabotage proof: change the success-branch return to ``raise outcome.code``;
    the test fails because no exception is expected.
    """
    db = sqlite3.connect(":memory:")
    create_schema(db)
    runner = BatchTransactionStageRunner(_OkStage(), db=db)
    ctx = FetchContext(source_name="src", item_id="item-001")
    outcome = runner.run_batch_critical(ctx)
    assert outcome.code == StatusCode.FETCH_OK
    db.close()


# ---------------------------------------------------------------------------
# WARN-severity routing — exercises _emit_outcome's branch
# ---------------------------------------------------------------------------


def test_isolated_runner_routes_warn_outcome_through_emit_warn() -> None:
    """WARN-severity outcomes go through ``emit.warn`` — the timeline
    row records ``severity='warn'``.

    Sabotage proof: in ``_emit_outcome`` swap the ``WARN`` branch to
    always call ``emit.ok``; the assertion ``rows[0][1] == 'warn'``
    fails.
    """
    db = sqlite3.connect(":memory:")
    create_schema(db)
    runner = IsolatedStageRunner(_WarnStage(), db=db)
    ctx = FetchContext(source_name="src", item_id="item-001")
    runner.run(ctx)
    rows = db.execute(
        "SELECT status_code, severity FROM pipeline_item_status WHERE source_name=? AND item_id=?",
        ("src", "item-001"),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "FETCH_THROTTLED"
    assert rows[0][1] == "warn"
    db.close()
