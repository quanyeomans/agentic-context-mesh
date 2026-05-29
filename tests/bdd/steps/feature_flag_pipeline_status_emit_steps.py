"""Step definitions for feature_flag_pipeline_status_emit.feature (ADR-025).

OFF branch: ``emit_for`` is invoked with ``db=None`` to model the
flag-OFF semantics (the call site reads ``flag('pipeline_status_emit')``
and passes ``db`` only when ON). No rows reach the table.

ON branch: ``emit_for`` is invoked with a live SQLite connection; rows
land in ``pipeline_item_status`` per ADR-025 Pattern B.

F1-clean: no @patch / module-attribute substitution on kairix.
F2-clean: no ``KAIRIX_*`` env-var manipulation.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest
from pytest_bdd import given, then, when

from kairix.core.db.schema import create_schema
from kairix.core.observability import StatusCode, emit_for

pytestmark = pytest.mark.bdd


@dataclass
class _ScenarioState:
    db: sqlite3.Connection
    db_for_emit: sqlite3.Connection | None = None


@pytest.fixture
def status_emit_state(tmp_path: Path) -> _ScenarioState:
    db = sqlite3.connect(str(tmp_path / "test.sqlite"))
    create_schema(db)
    state = _ScenarioState(db=db)
    yield state
    db.close()


@given("a fresh kairix database")
def _fresh_db(status_emit_state: _ScenarioState) -> None:
    # Fixture already opened a fresh DB; no-op here.
    rows = status_emit_state.db.execute("SELECT COUNT(*) FROM pipeline_item_status").fetchone()
    assert rows[0] == 0


@given("the pipeline_status_emit flag is OFF")
def _flag_off(status_emit_state: _ScenarioState) -> None:
    # Flag-OFF semantics: callers pass db=None to emit_for.
    status_emit_state.db_for_emit = None


@given("the pipeline_status_emit flag is ON")
def _flag_on(status_emit_state: _ScenarioState) -> None:
    status_emit_state.db_for_emit = status_emit_state.db


@when("the pipeline emits one extract status for an item")
def _emit_one(status_emit_state: _ScenarioState) -> None:
    with emit_for("sharepoint", "item-bdd-1", "extract", db=status_emit_state.db_for_emit) as emit:
        emit.ok(StatusCode.EXTRACT_OK, detail={"chars": 100})
    if status_emit_state.db_for_emit is not None:
        status_emit_state.db_for_emit.commit()


@then("the pipeline_item_status table is empty")
def _table_empty(status_emit_state: _ScenarioState) -> None:
    n = status_emit_state.db.execute("SELECT COUNT(*) FROM pipeline_item_status").fetchone()[0]
    assert n == 0


@then("the pipeline_item_status table contains exactly one row")
def _table_one_row(status_emit_state: _ScenarioState) -> None:
    n = status_emit_state.db.execute("SELECT COUNT(*) FROM pipeline_item_status").fetchone()[0]
    assert n == 1


@then("the row records the EXTRACT_OK status with severity ok")
def _row_shape(status_emit_state: _ScenarioState) -> None:
    row = status_emit_state.db.execute("SELECT status_code, severity FROM pipeline_item_status").fetchone()
    assert row == ("EXTRACT_OK", "ok")


__all__ = ["status_emit_state"]
