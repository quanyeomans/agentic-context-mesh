"""F54 integration parity for the pipeline_status_emit flag (ADR-025 §5 DoD 1.6).

Pins that the flag's two branches produce observably different outcomes
through the same call site — OFF leaves the timeline untouched; ON
appends one row per stage per item.

Flag resolution uses :class:`FakeFeatureFlagResolver` from
``tests/fakes.py`` (F1-clean: no @patch / module-attribute substitution
on kairix internals). The local dispatcher reads the flag and chooses
whether to pass ``db`` into ``emit_for`` (ON branch) or pass ``None``
(OFF branch — emit_for is a no-op context manager).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

from kairix.core.db.schema import create_schema
from kairix.core.observability import StatusCode, emit_for
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.integration


def _open_db(tmp_path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(str(tmp_path / "f54.sqlite"))
    create_schema(db)
    return db


def _emit_one(
    db: sqlite3.Connection,
    *,
    read_flag: Callable[[str], bool],
    item_id: str,
) -> None:
    """Local dispatcher modelling the call-site pattern: read the flag,
    then pass db or None into emit_for accordingly.
    """
    db_for_emit = db if read_flag("pipeline_status_emit") else None
    with emit_for("sharepoint", item_id, "extract", db=db_for_emit) as emit:
        emit.ok(StatusCode.EXTRACT_OK, detail={"chars": 100})
    if db_for_emit is not None:
        db_for_emit.commit()


def test_flag_off_no_writes(tmp_path: Path) -> None:
    db = _open_db(tmp_path)
    try:
        resolver = FakeFeatureFlagResolver().with_flag("pipeline_status_emit", False)
        _emit_one(db, read_flag=resolver.get, item_id="item-off")
        n = db.execute("SELECT COUNT(*) FROM pipeline_item_status").fetchone()[0]
        assert n == 0
    finally:
        db.close()


def test_flag_on_appends_one_row(tmp_path: Path) -> None:
    # F69-small-scale-only: flag-branch parity has no row-scale behaviour to validate.
    db = _open_db(tmp_path)
    try:
        resolver = FakeFeatureFlagResolver().with_flag("pipeline_status_emit", True)
        _emit_one(db, read_flag=resolver.get, item_id="item-on")
        rows = db.execute(
            "SELECT source_name, item_id, stage, status_code, severity FROM pipeline_item_status"
        ).fetchall()
        assert rows == [("sharepoint", "item-on", "extract", "EXTRACT_OK", "ok")]
    finally:
        db.close()


def test_off_then_on_in_sequence_only_on_writes(tmp_path: Path) -> None:
    # F69-small-scale-only: flag-branch parity check, no row-scale behaviour.
    """OFF then ON against the same DB — only the ON-branch emit lands."""
    db = _open_db(tmp_path)
    try:
        resolver_off = FakeFeatureFlagResolver().with_flag("pipeline_status_emit", False)
        _emit_one(db, read_flag=resolver_off.get, item_id="item-mix-off")
        resolver_on = FakeFeatureFlagResolver().with_flag("pipeline_status_emit", True)
        _emit_one(db, read_flag=resolver_on.get, item_id="item-mix-on")
        rows = db.execute("SELECT item_id FROM pipeline_item_status ORDER BY item_id").fetchall()
        assert rows == [("item-mix-on",)]
    finally:
        db.close()
