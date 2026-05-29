"""ADR-025 §5 DoD 1.1 + 1.7 — schema + append-only invariant.

Verifies the ``pipeline_item_status`` table lands via ``create_schema``
with the indexes + severity CHECK constraint per ADR-025 §8.

The append-only invariant (P6) is enforced via runtime discipline —
this test pins the discipline by asserting the timeline shape grows
through ``write_status`` and that the table accepts new rows but
``UPDATE pipeline_item_status`` succeeds at SQL level (we don't add
a trigger). The F76 import-ban prevents UPDATE from being added in
production code; this test pins that *the writer* never updates.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kairix.core.db.schema import create_schema
from kairix.core.observability import StatusCode, StatusRecord, write_status

pytestmark = pytest.mark.integration


def _open_db(tmp_path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(str(tmp_path / "test.sqlite"))
    create_schema(db)
    return db


def test_schema_creates_pipeline_item_status_table(tmp_path: Path) -> None:
    # F69-small-scale-only: schema existence assertion has no row-scale variant.
    db = _open_db(tmp_path)
    try:
        names = {
            r[0]
            for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='pipeline_item_status'"
            ).fetchall()
        }
        assert names == {"pipeline_item_status"}
    finally:
        db.close()


def test_schema_creates_pipeline_item_status_indexes(tmp_path: Path) -> None:
    # F69-small-scale-only: index metadata check, no row-scale dependency.
    db = _open_db(tmp_path)
    try:
        index_names = {
            r[0]
            for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='pipeline_item_status'"
            ).fetchall()
        }
        assert "idx_pipeline_status_lookup" in index_names
        assert "idx_pipeline_status_by_code" in index_names
    finally:
        db.close()


def test_severity_check_constraint_rejects_unknown(tmp_path: Path) -> None:
    db = _open_db(tmp_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO pipeline_item_status (source_name, item_id, stage, status_code, severity, occurred_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("sharepoint", "item-1", "extract", "EXTRACT_OK", "fatal", "2026-05-29T08:00:00+00:00"),
            )
    finally:
        db.close()


def test_write_status_appends_row(tmp_path: Path) -> None:
    db = _open_db(tmp_path)
    try:
        record = StatusRecord(
            source_name="sharepoint",
            item_id="item-42",
            stage="extract",
            status_code=StatusCode.EXTRACT_OK.name,
            severity=StatusCode.EXTRACT_OK.severity.value,
            detail_json=None,
            occurred_at="2026-05-29T08:00:00+00:00",
        )
        write_status(record, db=db)
        db.commit()
        row = db.execute(
            "SELECT source_name, item_id, stage, status_code, severity FROM pipeline_item_status"
        ).fetchone()
        assert row == ("sharepoint", "item-42", "extract", "EXTRACT_OK", "ok")
    finally:
        db.close()


def test_repeat_pk_collision_is_ignored_not_updated(tmp_path: Path) -> None:
    """P6 append-only: a re-insert with the same PK does NOT update existing data.

    INSERT OR IGNORE preserves the original row. The caller is responsible
    for advancing ``occurred_at`` on rapid successive emits.
    """
    db = _open_db(tmp_path)
    try:
        base = StatusRecord(
            source_name="sharepoint",
            item_id="item-99",
            stage="extract",
            status_code=StatusCode.EXTRACT_OK.name,
            severity="ok",
            detail_json='{"first": true}',
            occurred_at="2026-05-29T08:00:00+00:00",
        )
        write_status(base, db=db)
        # Same PK, different detail — must be ignored.
        clobber = StatusRecord(
            source_name="sharepoint",
            item_id="item-99",
            stage="extract",
            status_code=StatusCode.EXTRACT_DISK_FULL.name,
            severity="error",
            detail_json='{"second": true}',
            occurred_at="2026-05-29T08:00:00+00:00",
        )
        write_status(clobber, db=db)
        db.commit()
        row = db.execute(
            "SELECT status_code, severity, detail_json FROM pipeline_item_status WHERE item_id='item-99'"
        ).fetchone()
        assert row == ("EXTRACT_OK", "ok", '{"first": true}')
    finally:
        db.close()
