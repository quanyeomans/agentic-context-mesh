"""Unit-layer coverage lift for ``kairix.core.connectors.cc_pair_cli``.

The F30 outcome tests (``tests/integration/test_outcome_cc_pair_cli.py``)
exercise every verb via subprocess.run, but the unit layer needs the
same code paths instrumented for F7 (per-file ≥90% coverage). This
file drives every verb + JSON / text mode through the public
:func:`main` entry with the public :func:`default_db_provider` seam.

F1-clean / F2-clean / F5-clean: no @patch, no env-var manipulation,
no internal-name imports.
"""

from __future__ import annotations

import io
import json
import sqlite3
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from kairix.core.connectors.cc_pair import create_cc_pair, transition_cc_pair
from kairix.core.connectors.cc_pair_cli import default_db_provider
from kairix.core.connectors.cc_pair_cli import main as cc_pair_main
from kairix.core.db.schema import create_schema

pytestmark = pytest.mark.unit


def _build_db_with_connector(tmp_path: Path) -> Path:
    db_path = tmp_path / "kairix.sqlite"
    with closing(sqlite3.connect(str(db_path))) as db:
        create_schema(db, dims=4)
        now = "2026-05-23T00:00:00Z"
        db.execute(
            "INSERT INTO topology_connectors "
            "(kind, name, connector_specific_config, default_sensitivity, created_at, updated_at) "
            "VALUES ('obsidian', 'c1', '{}', 'internal', ?, ?)",
            (now, now),
        )
        db.commit()
    return db_path


def _run_main(db_path: Path, argv: list[str]) -> tuple[int, str, str]:
    """Helper — invoke main() with a tmp-path db_provider seam."""
    out, err = io.StringIO(), io.StringIO()

    def _provider(_explicit: Path | None) -> sqlite3.Connection:
        return sqlite3.connect(str(db_path))

    with redirect_stdout(out), redirect_stderr(err):
        code = cc_pair_main(argv, db_provider=_provider)
    return code, out.getvalue(), err.getvalue()


def test_main_list_empty_database_reports_friendly_line(tmp_path: Path) -> None:
    """The list verb on an empty DB renders the friendly empty-state line."""
    db_path = _build_db_with_connector(tmp_path)
    code, stdout, _ = _run_main(db_path, ["list"])
    assert code == 0
    assert "No cc_pairs declared" in stdout


def test_main_list_json_mode_emits_envelope(tmp_path: Path) -> None:
    """The list verb JSON mode emits a parseable envelope."""
    db_path = _build_db_with_connector(tmp_path)
    code, stdout, _ = _run_main(db_path, ["list", "--json"])
    assert code == 0
    parsed = json.loads(stdout)
    assert "cc_pairs" in parsed
    assert parsed["count"] == 0


def test_main_list_human_renders_seeded_row(tmp_path: Path) -> None:
    """After create, list returns the row in human mode (header + row)."""
    db_path = _build_db_with_connector(tmp_path)
    code, _, _ = _run_main(db_path, ["create", "--connector-id", "1", "--name", "pX"])
    assert code == 0
    code, stdout, _ = _run_main(db_path, ["list"])
    assert code == 0
    assert "NAME" in stdout  # header
    assert "pX" in stdout  # row


def test_main_list_filter_by_status(tmp_path: Path) -> None:
    """The --status filter passes through to list_cc_pairs."""
    db_path = _build_db_with_connector(tmp_path)
    _run_main(db_path, ["create", "--connector-id", "1", "--name", "p1"])
    code, stdout, _ = _run_main(db_path, ["list", "--status", "SCHEDULED"])
    assert code == 0
    assert "p1" in stdout
    # Non-matching status returns empty.
    code, stdout, _ = _run_main(db_path, ["list", "--status", "ACTIVE"])
    assert code == 0
    assert "No cc_pairs declared" in stdout


def test_main_create_human_mode_reports_scheduled(tmp_path: Path) -> None:
    """``create`` returns 0 + reports SCHEDULED in stdout."""
    db_path = _build_db_with_connector(tmp_path)
    code, stdout, _ = _run_main(db_path, ["create", "--connector-id", "1", "--name", "pX"])
    assert code == 0
    assert "status=SCHEDULED" in stdout
    assert "pX" in stdout


def test_main_create_json_envelope(tmp_path: Path) -> None:
    """``create --json`` emits ok=True + id + status."""
    db_path = _build_db_with_connector(tmp_path)
    code, stdout, _ = _run_main(db_path, ["create", "--connector-id", "1", "--name", "pX", "--json"])
    assert code == 0
    parsed = json.loads(stdout)
    assert parsed["ok"] is True
    assert parsed["status"] == "SCHEDULED"


def test_main_create_with_each_access_type(tmp_path: Path) -> None:
    """``--access-type`` accepts each documented value."""
    db_path = _build_db_with_connector(tmp_path)
    for i, access in enumerate(("PRIVATE", "PUBLIC", "SYNC"), start=1):
        code, stdout, _ = _run_main(
            db_path,
            ["create", "--connector-id", "1", "--name", f"p{i}", "--access-type", access],
        )
        assert code == 0
        assert f"p{i}" in stdout


def test_main_pause_from_scheduled_fails_with_friendly_stderr(tmp_path: Path) -> None:
    """SCHEDULED → PAUSED is illegal; error renders to stderr with fix:."""
    db_path = _build_db_with_connector(tmp_path)
    with closing(sqlite3.connect(str(db_path))) as db:
        create_cc_pair(db, connector_id=1, credential_id=None, name="p1")
        db.commit()
    code, _, stderr = _run_main(db_path, ["pause", "--id", "1"])
    assert code != 0
    assert "illegal transition" in stderr
    assert "fix:" in stderr


def test_main_pause_json_mode_envelope(tmp_path: Path) -> None:
    """``pause --json`` on an illegal transition returns ok=False JSON."""
    db_path = _build_db_with_connector(tmp_path)
    with closing(sqlite3.connect(str(db_path))) as db:
        create_cc_pair(db, connector_id=1, credential_id=None, name="p1")
        db.commit()
    code, _, stderr = _run_main(db_path, ["pause", "--id", "1", "--json"])
    assert code != 0
    parsed = json.loads(stderr)
    assert parsed["ok"] is False
    assert "illegal" in parsed["error"]


def test_main_resume_from_paused_returns_to_active(tmp_path: Path) -> None:
    """Full lifecycle path: SCHEDULED → INITIAL → ACTIVE → PAUSED → resume → ACTIVE."""
    db_path = _build_db_with_connector(tmp_path)
    with closing(sqlite3.connect(str(db_path))) as db:
        create_cc_pair(db, connector_id=1, credential_id=None, name="p1")
        transition_cc_pair(db, 1, "INITIAL_INDEXING")
        transition_cc_pair(db, 1, "ACTIVE")
        transition_cc_pair(db, 1, "PAUSED")
        db.commit()
    code, stdout, _ = _run_main(db_path, ["resume", "--id", "1"])
    assert code == 0
    assert "ACTIVE" in stdout


def test_main_delete_transitions_to_deleting(tmp_path: Path) -> None:
    """``delete`` on a SCHEDULED cc_pair transitions to DELETING."""
    db_path = _build_db_with_connector(tmp_path)
    _run_main(db_path, ["create", "--connector-id", "1", "--name", "p1"])
    code, stdout, _ = _run_main(db_path, ["delete", "--id", "1"])
    assert code == 0
    assert "DELETING" in stdout


def test_main_delete_unknown_id_returns_error(tmp_path: Path) -> None:
    """``delete`` on a missing id surfaces a not-found error to stderr."""
    db_path = _build_db_with_connector(tmp_path)
    code, _, stderr = _run_main(db_path, ["delete", "--id", "999"])
    assert code != 0
    assert "not found" in stderr


def test_main_delete_unknown_id_json_envelope(tmp_path: Path) -> None:
    """``delete --json`` on a missing id returns ok=False + not-found error key."""
    db_path = _build_db_with_connector(tmp_path)
    code, _, stderr = _run_main(db_path, ["delete", "--id", "999", "--json"])
    assert code != 0
    parsed = json.loads(stderr)
    assert parsed["ok"] is False
    assert "not found" in parsed["error"]


def test_default_db_provider_opens_explicit_path(tmp_path: Path) -> None:
    """The production provider opens an explicit path."""
    db_path = _build_db_with_connector(tmp_path)
    conn = default_db_provider(db_path)
    try:
        rows = conn.execute("SELECT COUNT(*) FROM topology_cc_pairs").fetchone()
        assert rows[0] == 0
    finally:
        conn.close()
