"""Step definitions for cli_maintenance.feature.

F46-compliant: the step impls compose through the warm step's public
API (:func:`ensure_sqlite_stats`) plus :func:`FakePaths` and the public
CLI entry point — no direct ``MaintenanceScheduler(...)`` /
``SearchPipeline(...)`` construction.
"""

from __future__ import annotations

import io
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, then, when

from kairix.core.db.schema import create_schema
from kairix.core.maintenance.cli import run_analyze_command
from kairix.platform.warm.sqlite_stats import ensure_sqlite_stats
from tests.fakes import FakePaths

pytestmark = pytest.mark.bdd


_NOW = "2026-06-04T00:00:00Z"


@pytest.fixture
def _stats_state(tmp_path: Path) -> Iterator[dict[str, Any]]:
    """Per-scenario state holder. Manages the SQLite connection lifecycle."""
    state: dict[str, Any] = {
        "db_path": tmp_path / "kairix.sqlite",
        "db": None,
        "paths": None,
        "result": None,
    }
    yield state
    if state["db"] is not None:
        state["db"].close()


def _seed_one_document(db: sqlite3.Connection) -> None:
    db.execute(
        "INSERT INTO documents (collection, path, hash, source_name, source_uri, "
        "source_modified_at, source_page, sensitivity, created_at, modified_at, active) "
        "VALUES ('default', 'doc.md', 'agent-alpha-1', NULL, NULL, NULL, NULL, 'public', ?, ?, 1)",
        (_NOW, _NOW),
    )
    db.commit()


# ---------------------------------------------------------------------------
# Givens
# ---------------------------------------------------------------------------


@given("a kairix index with documents but no sqlite_stat1 rows")
def _given_fresh_db_with_documents(_stats_state: dict[str, Any]) -> None:
    db = sqlite3.connect(str(_stats_state["db_path"]))
    create_schema(db, dims=4)
    _seed_one_document(db)
    _stats_state["db"] = db
    _stats_state["paths"] = FakePaths(
        db_path=_stats_state["db_path"],
        document_root=_stats_state["db_path"].parent / "vault",
    )


@given("a kairix index with sqlite_stat1 already populated")
def _given_db_with_stats(_stats_state: dict[str, Any]) -> None:
    db = sqlite3.connect(str(_stats_state["db_path"]))
    create_schema(db, dims=4)
    _seed_one_document(db)
    # Pre-populate sqlite_stat1 by running ANALYZE once outside the
    # warm step under test.
    db.execute("ANALYZE")
    db.commit()
    _stats_state["db"] = db
    _stats_state["paths"] = FakePaths(
        db_path=_stats_state["db_path"],
        document_root=_stats_state["db_path"].parent / "vault",
    )


# ---------------------------------------------------------------------------
# When
# ---------------------------------------------------------------------------


@when("the warm step ensure_sqlite_stats runs")
def _when_run_warm_step(_stats_state: dict[str, Any]) -> None:
    _stats_state["result"] = ensure_sqlite_stats(_stats_state["db"], _stats_state["paths"])


# ---------------------------------------------------------------------------
# Thens
# ---------------------------------------------------------------------------


@then('the step reports detail "ANALYZE complete"')
def _then_detail_analyze_complete(_stats_state: dict[str, Any]) -> None:
    """Sabotage: drop the ANALYZE branch in ensure_sqlite_stats and the
    skipped detail string appears instead — this assertion fires."""
    result = _stats_state["result"]
    assert result.detail == "ANALYZE complete", f"expected detail 'ANALYZE complete'; got {result.detail!r}"


@then("the index now has sqlite_stat1 rows present")
def _then_stat1_populated(_stats_state: dict[str, Any]) -> None:
    """Sabotage: replace ``db.execute('ANALYZE')`` with ``pass`` and the
    stat row count returns 0 — this assertion fires."""
    row = _stats_state["db"].execute("SELECT COUNT(*) FROM sqlite_stat1").fetchone()
    assert row is not None and int(row[0]) >= 1, f"expected at least one sqlite_stat1 row; got {row}"


@then('the step reports detail "stats already present, skipped"')
def _then_detail_skipped(_stats_state: dict[str, Any]) -> None:
    result = _stats_state["result"]
    assert result.detail == "stats already present, skipped", f"expected skipped detail; got {result.detail!r}"


@then("the step reports elapsed_ms equal to zero")
def _then_elapsed_zero(_stats_state: dict[str, Any]) -> None:
    """Sabotage: drop the early-return guard and ANALYZE re-runs even when
    stats are present — elapsed_ms becomes non-zero."""
    result = _stats_state["result"]
    assert result.elapsed_ms == 0.0, f"expected elapsed_ms=0; got {result.elapsed_ms}"


# ---------------------------------------------------------------------------
# CLI scenario — operator-driven kairix maintenance analyze
# ---------------------------------------------------------------------------


@given("a kairix process configured with FakePaths and a seeded index")
def _given_cli_seeded_index(_stats_state: dict[str, Any]) -> None:
    """Stand up a seeded SQLite index and close the connection.

    The CLI opens its own connection on the path; the BDD state holder
    keeps the path for the When-step to feed via ``--db-path``.
    """
    db = sqlite3.connect(str(_stats_state["db_path"]))
    create_schema(db, dims=4)
    _seed_one_document(db)
    db.close()
    _stats_state["db"] = None
    _stats_state["paths"] = FakePaths(
        db_path=_stats_state["db_path"],
        document_root=_stats_state["db_path"].parent / "vault",
    )


@when("the operator runs `kairix maintenance analyze` with valid input")
def _when_operator_runs_maintenance_analyze(_stats_state: dict[str, Any]) -> None:
    """Drive the CLI entry through its public main, capturing stdout."""
    buf = io.StringIO()
    rc = run_analyze_command(db_path=_stats_state["db_path"], out=buf, as_json=False)
    _stats_state["cli_rc"] = rc
    _stats_state["cli_stdout"] = buf.getvalue()


@then("the command exits 0 and prints the expected envelope")
def _then_cli_envelope(_stats_state: dict[str, Any]) -> None:
    """Pin every load-bearing assertion of the CLI's textual envelope.

    Sabotage: drop ``"plan before:"`` from the formatter and the
    ``assert "plan before:" in stdout`` line catches it. The rc check
    catches a regression that exits non-zero on the happy path.
    """
    assert _stats_state["cli_rc"] == 0, f"expected rc=0; got {_stats_state['cli_rc']}"
    stdout = _stats_state["cli_stdout"]
    assert "ANALYZE complete" in stdout, f"missing 'ANALYZE complete' in:\n{stdout}"
    assert "rows_analyzed=" in stdout
    assert "plan before:" in stdout
    assert "plan after:" in stdout
