"""Unit tests for :mod:`kairix.core.observability.dead_letter_status`.

Exercises the pure analysis core (classification, snapshot build,
JSON/human render) against a sandboxed SQLite database built through
``create_schema``. No monkeypatching, no env-var setup.

Sabotage notes per test are documented inline. The most load-bearing
test is ``test_build_status_buckets_per_failure_class``; the project
agent ran the mutate→fail→restore loop on that test before commit.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kairix.core.db.schema import create_schema
from kairix.core.observability.dead_letter_status import (
    OTHER_CLASS,
    DeadLetterStatusReport,
    build_status,
    classify_error,
    render_human,
    render_json,
)

pytestmark = pytest.mark.unit


def _open_db(tmp_path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(str(tmp_path / "kairix.sqlite"))
    create_schema(db)
    return db


def _insert_dead_letter(
    db: sqlite3.Connection,
    source_name: str,
    item_id: str,
    failure_count: int,
    last_error: str,
    last_attempt: str,
) -> None:
    db.execute(
        "INSERT INTO connector_deadletter "
        "(source_name, item_id, failure_count, last_error, last_attempt) "
        "VALUES (?, ?, ?, ?, ?)",
        (source_name, item_id, failure_count, last_error, last_attempt),
    )


def _insert_bronze(
    db: sqlite3.Connection,
    source_name: str,
    item_id: str,
    mime: str,
) -> None:
    db.execute(
        "INSERT INTO bronze_records (source_name, item_id, raw_path, mime, fetched_at) VALUES (?, ?, ?, ?, ?)",
        (source_name, item_id, f"/tmp/{item_id}", mime, "2026-05-26T05:50:00Z"),
    )


def test_build_status_empty_db_returns_zero_report(tmp_path: Path) -> None:
    """Empty connector_deadletter → total=0, per_source=().

    Sabotage: replacing ``int(sum(...))`` with ``1`` in build_status
    breaks the equality assertion.
    """
    db = _open_db(tmp_path)
    try:
        report = build_status(db)
        assert isinstance(report, DeadLetterStatusReport)
        assert report.total == 0
        assert report.per_source == ()
    finally:
        db.close()


def test_build_status_buckets_per_failure_class(tmp_path: Path) -> None:
    """Three rows with distinct classes land in three buckets.

    Sabotage: removing the ``forbidden_403`` rule from ``_CLASS_RULES``
    re-routes the 403 row into ``other`` and breaks the bucket
    membership assertion. The agent executed this mutation before
    commit and watched the test fail with a clear class-name mismatch.
    """
    db = _open_db(tmp_path)
    try:
        _insert_dead_letter(db, "connector-alpha", "i1", 3, "MissingDependencyException", "2026-05-26T05:58Z")
        _insert_dead_letter(db, "connector-alpha", "i2", 1, "403 Forbidden", "2026-05-27T10:01Z")
        _insert_dead_letter(db, "connector-alpha", "i3", 0, "weird new failure", "2026-05-28T11:00Z")
        db.commit()

        report = build_status(db)
        assert report.total == 3
        assert len(report.per_source) == 1
        src = report.per_source[0]
        classes = {b.failure_class: b.count for b in src.by_failure_class}
        assert classes["missing_dependency"] == 1
        assert classes["forbidden_403"] == 1
        assert classes[OTHER_CLASS] == 1
    finally:
        db.close()


def test_build_status_mime_joins_against_bronze_records(tmp_path: Path) -> None:
    """MIME bucket joins against bronze_records; missing rows → (unknown).

    Sabotage: swapping the LEFT JOIN to INNER JOIN drops the row with
    no bronze row, breaking the (unknown) count.
    """
    db = _open_db(tmp_path)
    try:
        _insert_dead_letter(db, "connector-alpha", "i1", 3, "boom", "2026-05-26T05:58Z")
        _insert_dead_letter(db, "connector-alpha", "i2", 1, "boom2", "2026-05-27T10:01Z")
        _insert_bronze(db, "connector-alpha", "i1", "application/pdf")
        db.commit()

        report = build_status(db)
        mimes = {b.mime: b.count for b in report.per_source[0].by_mime_top10}
        assert mimes["application/pdf"] == 1
        assert mimes["(unknown)"] == 1
    finally:
        db.close()


def test_build_status_oldest_5_sorted_ascending(tmp_path: Path) -> None:
    """oldest_5 returns up to 5 rows in ascending last_attempt order.

    Sabotage: flipping ORDER BY to DESC breaks the equality on
    ``oldest_5[0].item_id``.
    """
    db = _open_db(tmp_path)
    try:
        for i in range(7):
            _insert_dead_letter(
                db,
                "connector-alpha",
                f"item-{i}",
                1,
                "err",
                f"2026-05-{20 + i:02d}T00:00:00Z",
            )
        db.commit()

        report = build_status(db)
        oldest = report.per_source[0].oldest_5
        assert len(oldest) == 5
        assert oldest[0].item_id == "item-0"
        assert oldest[-1].item_id == "item-4"
    finally:
        db.close()


def test_build_status_source_filter_restricts_to_one(tmp_path: Path) -> None:
    """``source_name=...`` returns only the named source."""
    db = _open_db(tmp_path)
    try:
        _insert_dead_letter(db, "connector-alpha", "i1", 1, "x", "2026-05-26T05:58Z")
        _insert_dead_letter(db, "connector-beta", "i2", 1, "y", "2026-05-27T05:58Z")
        db.commit()

        report = build_status(db, source_name="connector-beta")
        assert len(report.per_source) == 1
        assert report.per_source[0].source_name == "connector-beta"
        assert report.total == 1
    finally:
        db.close()


def test_render_json_round_trips_snapshot(tmp_path: Path) -> None:
    """JSON envelope has the documented keys + nested lists."""
    db = _open_db(tmp_path)
    try:
        _insert_dead_letter(db, "connector-alpha", "i1", 3, "MissingDependencyException", "2026-05-26T05:58Z")
        db.commit()

        envelope = render_json(build_status(db))
        assert envelope["total"] == 1
        assert len(envelope["per_source"]) == 1
        src = envelope["per_source"][0]
        assert src["source_name"] == "connector-alpha"
        assert src["count"] == 1
        assert src["by_failure_count"] == [{"failure_count": 3, "count": 1}]
        assert {"class": "missing_dependency", "count": 1} in src["by_failure_class"]
    finally:
        db.close()


def test_render_human_empty_state_friendly(tmp_path: Path) -> None:
    db = _open_db(tmp_path)
    try:
        rendered = render_human(build_status(db))
        assert "no dead-letter state" in rendered
    finally:
        db.close()


def test_render_human_populated_includes_section_headers(tmp_path: Path) -> None:
    """Populated render includes every per-source section."""
    db = _open_db(tmp_path)
    try:
        _insert_dead_letter(db, "connector-alpha", "i1", 0, "transient", "2026-05-26T05:58Z")
        _insert_dead_letter(db, "connector-alpha", "i2", 3, "MissingDependencyException", "2026-05-27T10:01Z")
        _insert_dead_letter(db, "connector-alpha", "i3", 2, "Timed out fetching", "2026-05-28T11:00Z")
        _insert_bronze(db, "connector-alpha", "i1", "application/pdf")
        db.commit()

        rendered = render_human(build_status(db))
        assert "Dead-letter summary" in rendered
        assert "connector-alpha" in rendered
        assert "By failure_count" in rendered
        assert "By failure class" in rendered
        assert "By MIME" in rendered
        assert "Oldest failures" in rendered
        # fc=0 row should carry the "eligible for retry" label.
        assert "eligible for retry" in rendered
        # fc=3 row should carry the "poisoned" label.
        assert "poisoned" in rendered

    finally:
        db.close()


def test_render_human_truncates_long_last_error(tmp_path: Path) -> None:
    """oldest_5 entries truncate last_error to a renderer-friendly length."""
    db = _open_db(tmp_path)
    try:
        long_err = "X" * 1000
        _insert_dead_letter(db, "connector-alpha", "i1", 3, long_err, "2026-05-26T05:58Z")
        db.commit()

        report = build_status(db)
        entry = report.per_source[0].oldest_5[0]
        assert len(entry.last_error_truncated) < 1000
        assert entry.last_error_truncated.endswith("…")
    finally:
        db.close()


def test_classify_error_unknown_text_falls_to_other() -> None:
    """Anything unmatched bucket-ends in ``other``."""
    assert classify_error("a brand-new failure mode") == OTHER_CLASS
    assert classify_error(None) == OTHER_CLASS
    assert classify_error("") == OTHER_CLASS
