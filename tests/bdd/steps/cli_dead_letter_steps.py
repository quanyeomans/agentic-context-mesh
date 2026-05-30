"""Step definitions for cli_dead_letter.feature.

Drives the ``kairix dead-letter`` CLI subcommand through its public
adapter ``kairix.dead_letter_cli.main`` with the ``db_path=`` kwarg
seam — no monkeypatching of paths.py or env vars.

F1-clean: no @patch on kairix internals. F2-clean: no env-var
manipulation. F4-clean: paths.py owns env-var reads. F46-compliant:
the step impls invoke the CLI ``main`` entry point.
"""

from __future__ import annotations

import io
import json
import sqlite3
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, then, when

from kairix.core.db.schema import create_schema
from kairix.dead_letter_cli import main as dead_letter_main

pytestmark = pytest.mark.bdd


def _seed_db(tmp_path: Path, *, with_rows: bool) -> Path:
    """Build a kairix.sqlite file with the schema applied, optionally seeded."""
    db_path = tmp_path / "kairix.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    if with_rows:
        db.execute(
            "INSERT INTO connector_deadletter "
            "(source_name, item_id, failure_count, last_error, last_attempt) "
            "VALUES (?, ?, ?, ?, ?)",
            ("connector-alpha", "item-1", 3, "MissingDependencyException: pdfplumber", "2026-05-26T05:58:00Z"),
        )
        db.execute(
            "INSERT INTO connector_deadletter "
            "(source_name, item_id, failure_count, last_error, last_attempt) "
            "VALUES (?, ?, ?, ?, ?)",
            ("connector-alpha", "item-2", 1, "403 Forbidden", "2026-05-27T10:01:00Z"),
        )
        db.execute(
            "INSERT INTO bronze_records (source_name, item_id, raw_path, mime, fetched_at) VALUES (?, ?, ?, ?, ?)",
            ("connector-alpha", "item-1", "/tmp/agent-alpha/item-1.pdf", "application/pdf", "2026-05-26T05:50:00Z"),
        )
    db.commit()
    db.close()
    return db_path


@pytest.fixture
def _dl_state(tmp_path: Path) -> dict[str, Any]:
    """Per-scenario fresh state container."""
    return {
        "tmp_path": tmp_path,
        "db_path": None,
        "stdout": "",
        "exit_code": -1,
    }


@given("a fresh kairix database with no dead-letter rows")
def _fresh_db(_dl_state: dict[str, Any]) -> None:
    _dl_state["db_path"] = _seed_db(_dl_state["tmp_path"], with_rows=False)


@given("a kairix database seeded with dead-letter rows for one connector")
def _seeded_db(_dl_state: dict[str, Any]) -> None:
    _dl_state["db_path"] = _seed_db(_dl_state["tmp_path"], with_rows=True)


def _run(state: dict[str, Any], *, json_mode: bool) -> None:
    argv = ["status", "--json"] if json_mode else ["status"]
    buf = io.StringIO()
    with redirect_stdout(buf):
        exit_code = dead_letter_main(argv, db_path=state["db_path"])
    state["stdout"] = buf.getvalue()
    state["exit_code"] = exit_code if exit_code is not None else 0


@when("the operator runs the kairix dead-letter status command")
def _run_status(_dl_state: dict[str, Any]) -> None:
    _run(_dl_state, json_mode=False)


@when("the operator runs the kairix dead-letter status command with the --json flag")
def _run_status_json(_dl_state: dict[str, Any]) -> None:
    _run(_dl_state, json_mode=True)


@then("the dead-letter stdout reports an empty triage summary")
def _stdout_reports_empty(_dl_state: dict[str, Any]) -> None:
    assert "no dead-letter state" in _dl_state["stdout"], (
        f"expected friendly empty-state message; got: {_dl_state['stdout']!r}"
    )


@then("the dead-letter stdout reports the per-source breakdown")
def _stdout_reports_breakdown(_dl_state: dict[str, Any]) -> None:
    stdout = _dl_state["stdout"]
    assert "connector-alpha" in stdout, f"expected source name in output; got: {stdout!r}"
    assert "By failure_count" in stdout, f"expected failure-count header; got: {stdout!r}"
    assert "By failure class" in stdout, f"expected failure-class header; got: {stdout!r}"
    assert "By MIME" in stdout, f"expected MIME header; got: {stdout!r}"


@then("the dead-letter stdout parses as JSON with a total key and a per_source list")
def _stdout_parses_as_json(_dl_state: dict[str, Any]) -> None:
    parsed = json.loads(_dl_state["stdout"])
    assert "total" in parsed, f"expected 'total' key; got keys: {list(parsed)}"
    assert "per_source" in parsed, f"expected 'per_source' key; got keys: {list(parsed)}"
    assert isinstance(parsed["per_source"], list)
    assert parsed["total"] >= 1


@then("the dead-letter command exits with code 0")
def _command_exits_zero(_dl_state: dict[str, Any]) -> None:
    assert _dl_state["exit_code"] == 0, f"expected exit 0; got {_dl_state['exit_code']}"
