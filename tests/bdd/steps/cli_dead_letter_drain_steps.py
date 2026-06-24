"""Step definitions for cli_dead_letter_drain.feature (PR-5).

Drives the ``kairix dead-letter drain`` verb through its public adapter
``kairix.dead_letter_cli.main`` with the ``db_path=`` kwarg seam — no
monkeypatching of paths.py or env vars.

F1-clean: no @patch on kairix internals. F2-clean: no env-var
manipulation. F4-clean: paths.py owns env-var reads. F46-compliant:
the step impls invoke the CLI ``main`` entry point.
"""

from __future__ import annotations

import io
import sqlite3
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, then, when

from kairix.core.db.schema import create_schema
from kairix.dead_letter_cli import main as dead_letter_main

pytestmark = pytest.mark.bdd

# A permanently-unprocessable last_error + a known-unsupported MIME so the
# drain core's narrow eligibility fires for the seeded rows.
_ERR_DRAINABLE = "some other connector error"
_MIME_UNSUPPORTED = "application/msword"
_ORPHAN_A = "removed-sharepoint"
_ORPHAN_B = "removed-gdrive"


def _seed_orphan(db: sqlite3.Connection, source_name: str) -> None:
    """One drainable dead-letter + its unsupported-MIME bronze row."""
    db.execute(
        "INSERT INTO connector_deadletter "
        "(source_name, item_id, failure_count, last_error, last_attempt) "
        "VALUES (?, ?, ?, ?, ?)",
        (source_name, "poison-1", 3, _ERR_DRAINABLE, "2026-05-26T05:58:00Z"),
    )
    db.execute(
        "INSERT INTO bronze_records "
        "(source_name, item_id, raw_path, mime, fetched_at, content_hash) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (source_name, "poison-1", "/bronze/poison-1", _MIME_UNSUPPORTED, "2026-05-26T05:50:00Z", f"h-{source_name}"),
    )


def _seed_db(tmp_path: Path, sources: tuple[str, ...]) -> Path:
    db_path = tmp_path / "kairix.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    for src in sources:
        _seed_orphan(db, src)
    db.commit()
    db.close()
    return db_path


def _remaining(db_path: Path, source_name: str) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM connector_deadletter WHERE source_name = ?",
                (source_name,),
            ).fetchone()[0]
        )
    finally:
        conn.close()


@pytest.fixture
def _drain_state(tmp_path: Path) -> dict[str, Any]:
    return {"tmp_path": tmp_path, "db_path": None, "stdout": "", "exit_code": -1}


@given("a kairix database with a drainable dead-letter for an orphaned source")
def _one_orphan(_drain_state: dict[str, Any]) -> None:
    _drain_state["db_path"] = _seed_db(_drain_state["tmp_path"], (_ORPHAN_A,))


@given("a kairix database with drainable dead-letters for two orphaned sources")
def _two_orphans(_drain_state: dict[str, Any]) -> None:
    _drain_state["db_path"] = _seed_db(_drain_state["tmp_path"], (_ORPHAN_A, _ORPHAN_B))


def _run(state: dict[str, Any], argv: list[str]) -> None:
    buf = io.StringIO()
    with redirect_stdout(buf):
        exit_code = dead_letter_main(argv, db_path=state["db_path"])
    state["stdout"] = buf.getvalue()
    state["exit_code"] = exit_code if exit_code is not None else 0


@when("the operator runs the kairix dead-letter drain command for that source")
def _run_drain_one(_drain_state: dict[str, Any]) -> None:
    _run(_drain_state, ["drain", _ORPHAN_A])


@when("the operator runs the kairix dead-letter drain command for all sources")
def _run_drain_all(_drain_state: dict[str, Any]) -> None:
    _run(_drain_state, ["drain"])


@when("the operator runs the kairix dead-letter drain command in dry-run mode")
def _run_drain_dry(_drain_state: dict[str, Any]) -> None:
    _run(_drain_state, ["drain", _ORPHAN_A, "--dry-run"])


@then("the drain stdout reports one row drained for that source")
def _reports_one_drained(_drain_state: dict[str, Any]) -> None:
    out = _drain_state["stdout"]
    assert _ORPHAN_A in out, f"expected source name in output; got: {out!r}"
    assert "drained 1" in out, f"expected 'drained 1'; got: {out!r}"


@then("the drain stdout reports a total across two sources")
def _reports_two_total(_drain_state: dict[str, Any]) -> None:
    out = _drain_state["stdout"]
    assert "across 2 source(s)" in out, f"expected two-source total; got: {out!r}"


@then("the drain stdout reports what would drain in dry-run mode")
def _reports_dry_run(_drain_state: dict[str, Any]) -> None:
    out = _drain_state["stdout"]
    assert "dry-run" in out, f"expected dry-run marker; got: {out!r}"
    assert "would drain 1" in out, f"expected 'would drain 1'; got: {out!r}"


@then("the orphaned source has no remaining dead-letter rows")
def _orphan_drained(_drain_state: dict[str, Any]) -> None:
    assert _remaining(_drain_state["db_path"], _ORPHAN_A) == 0


@then("both orphaned sources have no remaining dead-letter rows")
def _both_drained(_drain_state: dict[str, Any]) -> None:
    assert _remaining(_drain_state["db_path"], _ORPHAN_A) == 0
    assert _remaining(_drain_state["db_path"], _ORPHAN_B) == 0


@then("the orphaned source still has its dead-letter row")
def _orphan_untouched(_drain_state: dict[str, Any]) -> None:
    assert _remaining(_drain_state["db_path"], _ORPHAN_A) == 1


@then("the drain command exits with code 0")
def _exits_zero(_drain_state: dict[str, Any]) -> None:
    assert _drain_state["exit_code"] == 0, f"expected exit 0; got {_drain_state['exit_code']}"
