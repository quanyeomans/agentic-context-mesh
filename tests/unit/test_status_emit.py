"""ADR-025 §4 Pattern B — emit_for context manager behaviour.

Pins:
- ``emit.ok / warn / error`` writes to the table (flag-ON / db != None).
- Bare exit without an emit fires ``PIPELINE_STAGE_NO_EMIT`` (P1 fail-safe).
- Exception before emit records the exception detail + re-raises.
- Stage mismatch annotates the detail rather than dropping the emit.
- ``db=None`` is the flag-OFF no-op path.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from kairix.core.db.schema import create_schema
from kairix.core.observability import StatusCode, emit_for

pytestmark = pytest.mark.unit


def _open_db(tmp_path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(str(tmp_path / "test.sqlite"))
    create_schema(db)
    return db


def _rows(db: sqlite3.Connection) -> list[tuple[str, str, str, str | None]]:
    return [
        (r[0], r[1], r[2], r[3])
        for r in db.execute("SELECT stage, status_code, severity, detail_json FROM pipeline_item_status").fetchall()
    ]


def test_emit_ok_records_one_row(tmp_path: Path) -> None:
    db = _open_db(tmp_path)
    try:
        with emit_for("sharepoint", "item-1", "extract", db=db) as emit:
            emit.ok(StatusCode.EXTRACT_OK, detail={"chars": 4321})
        db.commit()
        assert _rows(db) == [("extract", "EXTRACT_OK", "ok", json.dumps({"chars": 4321}))]
    finally:
        db.close()


def test_emit_error_records_severity(tmp_path: Path) -> None:
    db = _open_db(tmp_path)
    try:
        with emit_for("sharepoint", "item-2", "extract", db=db) as emit:
            emit.error(StatusCode.EXTRACT_DISK_FULL, detail={"errno": 28})
        db.commit()
        row = db.execute("SELECT status_code, severity FROM pipeline_item_status WHERE item_id='item-2'").fetchone()
        assert row == ("EXTRACT_DISK_FULL", "error")
    finally:
        db.close()


def test_bare_exit_fires_pipeline_stage_no_emit(tmp_path: Path) -> None:
    db = _open_db(tmp_path)
    try:
        with emit_for("sharepoint", "item-3", "extract", db=db):
            pass  # body returns without calling emit
        db.commit()
        rows = _rows(db)
        assert len(rows) == 1
        assert rows[0][1] == "PIPELINE_STAGE_NO_EMIT"
        assert rows[0][2] == "error"
        # detail records the bare-exit reason
        assert "stage_exited_clean_without_emit" in (rows[0][3] or "")
    finally:
        db.close()


def test_exception_before_emit_records_traceback_and_reraises(tmp_path: Path) -> None:
    db = _open_db(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="boom"):
            with emit_for("sharepoint", "item-4", "extract", db=db):
                raise RuntimeError("boom")
        db.commit()
        rows = _rows(db)
        assert len(rows) == 1
        assert rows[0][1] == "PIPELINE_STAGE_NO_EMIT"
        detail = json.loads(rows[0][3] or "{}")
        assert detail["exception_class"] == "RuntimeError"
        assert "boom" in detail["exception_message"]
        assert "traceback_tail" in detail
    finally:
        db.close()


def test_exception_after_emit_does_not_double_record(tmp_path: Path) -> None:
    db = _open_db(tmp_path)
    try:
        with pytest.raises(RuntimeError):
            with emit_for("sharepoint", "item-5", "extract", db=db) as emit:
                emit.warn(StatusCode.EXTRACT_OK_EMPTY)
                raise RuntimeError("later")
        db.commit()
        rows = _rows(db)
        # One row: the EXTRACT_OK_EMPTY emit. No NO_EMIT fail-safe added
        # because the body did emit before raising.
        assert len(rows) == 1
        assert rows[0][1] == "EXTRACT_OK_EMPTY"
    finally:
        db.close()


def test_flag_off_no_op_with_db_none() -> None:
    """db=None puts emit_for in flag-OFF mode — calls accepted, no writes."""
    with emit_for("sharepoint", "item-6", "extract", db=None) as emit:
        emit.ok(StatusCode.EXTRACT_OK)
    # No assertion on a DB — the contract is "doesn't raise, doesn't write".


def test_stage_mismatch_annotates_detail(tmp_path: Path) -> None:
    """If a caller uses a code from a different stage, emit still records
    but annotates detail with the mismatch so it surfaces at inspect time.
    """
    db = _open_db(tmp_path)
    try:
        with emit_for("sharepoint", "item-7", "extract", db=db) as emit:
            # FETCH_OK is fetch-stage; we're in extract-stage context
            emit.ok(StatusCode.FETCH_OK, detail={"caller": "test"})
        db.commit()
        row = db.execute("SELECT detail_json FROM pipeline_item_status WHERE item_id='item-7'").fetchone()
        detail = json.loads(row[0])
        assert detail["_stage_mismatch"] == "code.stage=fetch ctx.stage=extract"
    finally:
        db.close()
