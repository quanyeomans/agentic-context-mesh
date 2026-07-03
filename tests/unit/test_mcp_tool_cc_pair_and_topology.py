"""Unit-layer coverage lift for the Wave D MCP surfaces in
``kairix/agents/mcp/server.py``:

* ``tool_features_status(topology=True)`` topology branch.
* ``tool_cc_pair`` escalation stub for every verb.

F1-clean / F2-clean / F5-clean: no @patch, no env-var manipulation,
no internal-name imports. The ``read_db_path`` DI seam on
``tool_features_status`` keeps the SQLite read out of the unit layer.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from kairix.agents.mcp.server import tool_cc_pair, tool_features_status

pytestmark = pytest.mark.unit


def _bootstrap_db(tmp_path: Path) -> Path:
    """Make a fresh schema-applied SQLite db at the tmp path."""
    db_path = tmp_path / "kairix.sqlite"
    with closing(sqlite3.connect(str(db_path))) as db:
        from kairix.core.db.schema import create_schema

        create_schema(db, dims=4)
    return db_path


def test_tool_cc_pair_list_envelope_contains_command() -> None:
    """Default verb ``list`` renders the OperatorOnlyCapability envelope."""
    env = tool_cc_pair()
    assert env["capability"] == "cc-pair"
    assert "kairix cc-pair list" in env["operator_command"]


def test_tool_cc_pair_pause_envelope_carries_id_placeholder() -> None:
    """Mutating verbs render with the --id <id> placeholder."""
    env = tool_cc_pair(verb="pause")
    assert "kairix cc-pair pause --id <id>" in env["operator_command"]


def test_tool_cc_pair_resume_envelope_carries_runtime_estimate() -> None:
    """Every cc-pair envelope carries the 5s runtime expectation."""
    env = tool_cc_pair(verb="resume")
    assert env["expected_runtime_seconds"] == 5
    assert "kairix cc-pair resume" in env["operator_command"]


def test_tool_features_status_without_topology_omits_key() -> None:
    """Default invocation (no topology kwarg) → no topology key in envelope."""
    env = tool_features_status()
    assert "flags" in env
    assert "topology" not in env
    assert env["error"] == ""


def test_tool_features_status_with_topology_adds_key(tmp_path: Path) -> None:
    """topology=True merges the topology diagnostics into the envelope.

    Uses the read_db_path seam so no env vars are mutated (F2 clean).
    """
    db_path = _bootstrap_db(tmp_path)
    env = tool_features_status(topology=True, read_db_path=lambda: db_path)
    assert "topology" in env
    assert env["topology"]["cc_pairs"] == []


def test_tool_features_status_topology_degrades_on_missing_schema(tmp_path: Path) -> None:
    """A path without schema → zero-snapshot rather than crash."""
    bad_db = tmp_path / "no_schema.sqlite"
    sqlite3.connect(str(bad_db)).close()
    env = tool_features_status(topology=True, read_db_path=lambda: bad_db)
    assert env["topology"] == {"cc_pairs": [], "actor_scopes": []}
