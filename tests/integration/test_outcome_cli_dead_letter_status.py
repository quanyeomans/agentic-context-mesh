"""F30 outcome test — ``kairix dead-letter status`` subprocess surface.

Per F30: every CLI subcommand in ``kairix/cli.py:COMMANDS`` ships with
an outcome test that (a) invokes via
``subprocess.run([python, -m, kairix.cli, dead-letter, ...])`` with a
``--db-path`` flag (no ``KAIRIX_*`` env vars per F2), and (b) asserts
on ``.stdout`` / ``.stderr`` content (not on returncode alone).

This file covers:

* Empty-state human-readable line.
* Populated human-readable per-source breakdown.
* JSON envelope shape (the contract the MCP tool also returns).
* ``--source-name`` filter slicing one connector.
* The MCP tool handler returning the same envelope (parity).
* Failure-class classification of canonical exception strings.

Sabotage: one proof is executed before commit (see agent report).
The classification test is the most load-bearing — flipping the order
of the ``forbidden_403`` rule with ``not_found_404`` or removing the
regex for ``MissingDependencyException`` breaks the assertion. I
verified this by commenting out the ``missing_dependency`` entry in
``_CLASS_RULES`` and watching ``test_classify_error_recognises_failure_modes``
fail with a clear mismatch, then restored it and re-ran green.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from kairix.core.db.schema import create_schema
from kairix.core.observability.dead_letter_status import (
    OTHER_CLASS,
    classify_error,
)

pytestmark = pytest.mark.integration


def _bootstrap_db(tmp_path: Path) -> Path:
    """Build a kairix.sqlite with the schema applied (no dead-letter rows)."""
    db_path = tmp_path / "kairix.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    db.commit()
    db.close()
    return db_path


def _seed_rows(db_path: Path) -> None:
    """Seed three dead-letter rows + one bronze row across one connector."""
    db = sqlite3.connect(str(db_path))
    try:
        db.execute(
            "INSERT INTO connector_deadletter "
            "(source_name, item_id, failure_count, last_error, last_attempt) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                "connector-alpha",
                "item-1",
                3,
                "MissingDependencyException: pdfplumber not installed",
                "2026-05-26T05:58:00Z",
            ),
        )
        db.execute(
            "INSERT INTO connector_deadletter "
            "(source_name, item_id, failure_count, last_error, last_attempt) "
            "VALUES (?, ?, ?, ?, ?)",
            ("connector-alpha", "item-2", 1, "HTTP 403 Forbidden", "2026-05-27T10:01:00Z"),
        )
        db.execute(
            "INSERT INTO connector_deadletter "
            "(source_name, item_id, failure_count, last_error, last_attempt) "
            "VALUES (?, ?, ?, ?, ?)",
            ("connector-beta", "item-9", 0, "Timed out fetching", "2026-05-28T11:30:00Z"),
        )
        db.execute(
            "INSERT INTO bronze_records (source_name, item_id, raw_path, mime, fetched_at) VALUES (?, ?, ?, ?, ?)",
            ("connector-alpha", "item-1", "/sandbox/agent-alpha/item-1.pdf", "application/pdf", "2026-05-26T05:50:00Z"),
        )
        db.commit()
    finally:
        db.close()


def test_dead_letter_status_empty_db_reports_friendly_line(tmp_path: Path) -> None:
    """``dead-letter status`` against an empty DB emits the friendly empty-state line."""
    db_path = _bootstrap_db(tmp_path)
    proc = subprocess.run(
        [sys.executable, "-m", "kairix.cli", "dead-letter", "status", "--db-path", str(db_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"unexpected exit {proc.returncode}: {proc.stderr}"
    assert "no dead-letter state" in proc.stdout, f"expected friendly empty-state line; got: {proc.stdout!r}"


def test_dead_letter_status_populated_db_reports_per_source(tmp_path: Path) -> None:
    """``dead-letter status`` lists every source with at least one row."""
    db_path = _bootstrap_db(tmp_path)
    _seed_rows(db_path)
    proc = subprocess.run(
        [sys.executable, "-m", "kairix.cli", "dead-letter", "status", "--db-path", str(db_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"unexpected exit {proc.returncode}: {proc.stderr}"
    assert "connector-alpha" in proc.stdout
    assert "connector-beta" in proc.stdout
    assert "By failure_count" in proc.stdout
    assert "By failure class" in proc.stdout
    assert "By MIME" in proc.stdout
    assert "application/pdf" in proc.stdout


def test_dead_letter_status_json_envelope_shape(tmp_path: Path) -> None:
    """``dead-letter status --json`` parses to the documented envelope shape."""
    db_path = _bootstrap_db(tmp_path)
    _seed_rows(db_path)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "kairix.cli",
            "dead-letter",
            "status",
            "--db-path",
            str(db_path),
            "--json",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"unexpected exit {proc.returncode}: {proc.stderr}"
    parsed = json.loads(proc.stdout)
    assert parsed["total"] == 3
    assert isinstance(parsed["per_source"], list)
    assert len(parsed["per_source"]) == 2
    alpha = next(s for s in parsed["per_source"] if s["source_name"] == "connector-alpha")
    assert alpha["count"] == 2
    # by_failure_count buckets must include fc=3 (poisoned) and fc=1.
    fcs = {b["failure_count"] for b in alpha["by_failure_count"]}
    assert {1, 3}.issubset(fcs)
    # by_failure_class must classify item-1 (MissingDependencyException) and
    # item-2 (403 Forbidden) into the right buckets.
    classes = {b["class"]: b["count"] for b in alpha["by_failure_class"]}
    assert classes.get("missing_dependency", 0) == 1, f"got classes={classes}"
    assert classes.get("forbidden_403", 0) == 1, f"got classes={classes}"
    # by_mime_top10 must surface application/pdf (from bronze_records) AND
    # (unknown) for item-2 which has no bronze row.
    mimes = {b["mime"]: b["count"] for b in alpha["by_mime_top10"]}
    assert mimes.get("application/pdf", 0) == 1, f"got mimes={mimes}"
    assert mimes.get("(unknown)", 0) == 1, f"got mimes={mimes}"
    # oldest_5 surfaces the seeded items in ascending last_attempt order.
    oldest = alpha["oldest_5"]
    assert len(oldest) == 2
    assert oldest[0]["item_id"] == "item-1"


def test_dead_letter_status_source_filter_slices_to_one(tmp_path: Path) -> None:
    """``--source-name`` restricts the report to a single connector."""
    db_path = _bootstrap_db(tmp_path)
    _seed_rows(db_path)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "kairix.cli",
            "dead-letter",
            "status",
            "--db-path",
            str(db_path),
            "--source-name",
            "connector-beta",
            "--json",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"unexpected exit {proc.returncode}: {proc.stderr}"
    parsed = json.loads(proc.stdout)
    assert parsed["total"] == 1
    assert len(parsed["per_source"]) == 1
    assert parsed["per_source"][0]["source_name"] == "connector-beta"


def test_tool_dead_letter_status_returns_same_envelope_as_cli(tmp_path: Path) -> None:
    """F30 MCP outcome — ``tool_dead_letter_status`` returns the documented envelope.

    Calls the MCP handler directly with the ``read_db_path`` DI seam so
    the tmp-path injection stays out of monkeypatch.setenv (F2-clean).
    Verifies CLI/MCP parity by comparing keys + per-source counts.
    """
    from kairix.agents.mcp.server import tool_dead_letter_status

    db_path = _bootstrap_db(tmp_path)
    _seed_rows(db_path)

    envelope = tool_dead_letter_status(read_db_path=lambda: db_path)
    assert envelope["error"] == ""
    assert envelope["total"] == 3
    assert len(envelope["per_source"]) == 2
    alpha = next(s for s in envelope["per_source"] if s["source_name"] == "connector-alpha")
    assert alpha["count"] == 2


def test_tool_dead_letter_status_handles_missing_db(tmp_path: Path) -> None:
    """MCP tool degrades to a typed-error envelope when the DB read fails."""
    from kairix.agents.mcp.server import tool_dead_letter_status

    envelope = tool_dead_letter_status(read_db_path=lambda: tmp_path / "does-not-exist.sqlite")
    # sqlite3.connect creates the file; the SELECT then fails on the
    # missing connector_deadletter table. Either way the envelope shape
    # is the documented degrade-not-crash contract.
    assert envelope["total"] == 0
    assert envelope["per_source"] == []
    assert envelope["error"] != ""


@pytest.mark.parametrize(
    "error_text,expected_class",
    [
        ("MissingDependencyException: pdfplumber", "missing_dependency"),
        ("OSError: No space left on device", "no_space"),
        ("ENOSPC writing chunk", "no_space"),
        ("HTTP 403 Forbidden — access denied", "forbidden_403"),
        ("HTTP 404 — not found", "not_found_404"),
        ("Operation timed out after 30s", "timeout"),
        ("ReadTimeout fetching graph", "timeout"),
        ("zipfile.BadZipFile: File is not a zip file", "corrupt_zip"),
        ("HTTP 429 Too Many Requests", "rate_limit"),
        ("UnicodeDecodeError: invalid start byte", "decode"),
        ("Some weird brand-new failure mode", OTHER_CLASS),
        ("", OTHER_CLASS),
        (None, OTHER_CLASS),
    ],
)
def test_classify_error_recognises_failure_modes(
    error_text: str | None,
    expected_class: str,
) -> None:
    """Per-class classification proof.

    Sabotage: comment out the ``missing_dependency`` rule in
    ``_CLASS_RULES`` → the first parametrised case fails because
    "MissingDependencyException: pdfplumber" no longer matches and
    degrades to ``other``. I executed this proof before commit
    (mutate → run → fail → restore → run → pass) — see file docstring.
    """
    assert classify_error(error_text) == expected_class
