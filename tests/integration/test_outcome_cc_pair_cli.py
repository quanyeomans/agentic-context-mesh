"""F30 outcome test — ``kairix cc-pair`` subprocess surface (Wave D).

Per F30 + the test-discipline spec: every CLI subcommand in
``kairix/cli.py:COMMANDS`` ships with at least one test that:

  (a) invokes via ``subprocess.run([python, -m, kairix.cli, cc-pair, ...])``
      with a ``--db-path`` flag (no ``KAIRIX_*`` env vars per F2);
  (b) asserts on ``.stdout`` / ``.stderr`` content (not on returncode alone,
      not on internal fake call counts).

This file covers all 5 verbs (list / create / pause / resume / delete)
+ the JSON envelope mode + the illegal-transition error path.

Sabotage-proof: see ``tests/unit/test_topology_validators.py`` for
the 5 cross-reference validator proofs. For these CLI outcome tests, I
verified each verb fails when the verb's rendering line is removed
(commented out the ``return 0, "created cc_pair ..."`` body → the
``test_cc_pair_create_*`` tests failed on the stdout assertion).
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from kairix.core.db.schema import create_schema

pytestmark = pytest.mark.integration


def _bootstrap_db(tmp_path: Path) -> Path:
    """Create a fresh kairix.sqlite with the topology schema applied."""
    db_path = tmp_path / "kairix.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db, dims=4)
    now = "2026-05-23T00:00:00Z"
    db.execute(
        "INSERT INTO topology_connectors "
        "(kind, name, connector_specific_config, default_sensitivity, created_at, updated_at) "
        "VALUES ('obsidian', 'obsidian-personal-conn', '{}', 'internal', ?, ?)",
        (now, now),
    )
    db.commit()
    db.close()
    return db_path


def test_cc_pair_list_empty_database_reports_no_cc_pairs(tmp_path: Path) -> None:
    """``cc-pair list`` on an empty DB emits the friendly empty-state line."""
    db_path = _bootstrap_db(tmp_path)
    proc = subprocess.run(
        [sys.executable, "-m", "kairix.cli", "cc-pair", "--db-path", str(db_path), "list"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"unexpected exit {proc.returncode}: {proc.stderr}"
    assert "No cc_pairs declared" in proc.stdout, f"expected friendly empty-state line; got: {proc.stdout!r}"


def test_cc_pair_list_json_mode_emits_envelope(tmp_path: Path) -> None:
    """``cc-pair list --json`` emits a parseable envelope with cc_pairs + count keys."""
    db_path = _bootstrap_db(tmp_path)
    proc = subprocess.run(
        [sys.executable, "-m", "kairix.cli", "cc-pair", "--db-path", str(db_path), "list", "--json"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"unexpected exit {proc.returncode}: {proc.stderr}"
    parsed = json.loads(proc.stdout)
    assert "cc_pairs" in parsed
    assert "count" in parsed
    assert parsed["count"] == 0


def test_cc_pair_create_inserts_row_at_status_scheduled(tmp_path: Path) -> None:
    """``cc-pair create`` returns 0 and reports SCHEDULED in stdout."""
    db_path = _bootstrap_db(tmp_path)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "kairix.cli",
            "cc-pair",
            "--db-path",
            str(db_path),
            "create",
            "--connector-id",
            "1",
            "--name",
            "obsidian-personal",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"unexpected exit {proc.returncode}: {proc.stderr}"
    assert "status=SCHEDULED" in proc.stdout
    assert "obsidian-personal" in proc.stdout

    # And a subsequent list sees the row.
    list_proc = subprocess.run(
        [sys.executable, "-m", "kairix.cli", "cc-pair", "--db-path", str(db_path), "list"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert "obsidian-personal" in list_proc.stdout
    assert "SCHEDULED" in list_proc.stdout


def test_cc_pair_pause_from_scheduled_fails_with_operator_friendly_message(tmp_path: Path) -> None:
    """SCHEDULED → PAUSED is illegal (state machine §3); error renders to stderr."""
    db_path = _bootstrap_db(tmp_path)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "kairix.cli",
            "cc-pair",
            "--db-path",
            str(db_path),
            "create",
            "--connector-id",
            "1",
            "--name",
            "obsidian-personal",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    proc = subprocess.run(
        [sys.executable, "-m", "kairix.cli", "cc-pair", "--db-path", str(db_path), "pause", "--id", "1"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode != 0, f"expected non-zero exit; got 0 (stdout={proc.stdout!r})"
    assert "illegal transition" in proc.stderr
    assert "fix:" in proc.stderr


def test_cc_pair_resume_from_paused_returns_to_active(tmp_path: Path) -> None:
    """Full lifecycle path: create → scheduled → indexing → active → paused → resume → active."""
    db_path = _bootstrap_db(tmp_path)
    # Seed via Python helpers then exercise the resume verb.
    db = sqlite3.connect(str(db_path))
    from kairix.core.connectors.cc_pair import create_cc_pair, transition_cc_pair

    create_cc_pair(db, connector_id=1, credential_id=None, name="obsidian-personal")
    transition_cc_pair(db, 1, "INITIAL_INDEXING")
    transition_cc_pair(db, 1, "ACTIVE")
    transition_cc_pair(db, 1, "PAUSED")
    db.commit()
    db.close()

    proc = subprocess.run(
        [sys.executable, "-m", "kairix.cli", "cc-pair", "--db-path", str(db_path), "resume", "--id", "1"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"unexpected exit {proc.returncode}: {proc.stderr}"
    assert "ACTIVE" in proc.stdout


def test_cc_pair_delete_transitions_to_deleting(tmp_path: Path) -> None:
    """``cc-pair delete`` transitions SCHEDULED → DELETING (state machine terminal)."""
    db_path = _bootstrap_db(tmp_path)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "kairix.cli",
            "cc-pair",
            "--db-path",
            str(db_path),
            "create",
            "--connector-id",
            "1",
            "--name",
            "obsidian-personal",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    proc = subprocess.run(
        [sys.executable, "-m", "kairix.cli", "cc-pair", "--db-path", str(db_path), "delete", "--id", "1"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"unexpected exit {proc.returncode}: {proc.stderr}"
    assert "DELETING" in proc.stdout


def test_cc_pair_delete_unknown_id_returns_error(tmp_path: Path) -> None:
    """``cc-pair delete --id 999`` against a fresh DB surfaces a "not found" error."""
    db_path = _bootstrap_db(tmp_path)
    proc = subprocess.run(
        [sys.executable, "-m", "kairix.cli", "cc-pair", "--db-path", str(db_path), "delete", "--id", "999"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode != 0
    assert "not found" in proc.stderr


def test_mcp_tool_cc_pair_returns_operator_only_envelope() -> None:
    """F30 outcome — ``tool_cc_pair`` returns the OperatorOnlyCapability envelope.

    Calls the MCP tool handler directly (per the F30 spec for MCP tools)
    and asserts on the returned envelope content — operator_command +
    reason + expected_runtime_seconds. The escalation envelope is the
    contract agents read to decide whether to escalate to a human
    operator.
    """
    from kairix.agents.mcp.server import tool_cc_pair

    envelope = tool_cc_pair(verb="list")
    assert envelope["capability"] == "cc-pair"
    assert "kairix cc-pair list" in envelope["operator_command"]
    assert "topology" in envelope["reason"]
    assert envelope["expected_runtime_seconds"] == 5

    pause_envelope = tool_cc_pair(verb="pause")
    assert "kairix cc-pair pause" in pause_envelope["operator_command"]
