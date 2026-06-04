"""Step definitions for mcp_maintenance_analyze.feature.

F46-compliant: the agent path calls the public ``tool_maintenance_analyze``
handler directly with ``db_path=`` injected — no FastMCP server, no
direct ``MaintenanceScheduler(...)`` construction.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, then, when

from kairix.agents.mcp.server import tool_maintenance_analyze
from kairix.core.db.schema import create_schema
from tests.fakes import FakePaths

pytestmark = pytest.mark.bdd


_NOW = "2026-06-04T00:00:00Z"


@pytest.fixture
def _mcp_state(tmp_path: Path) -> Iterator[dict[str, Any]]:
    state: dict[str, Any] = {
        "db_path": tmp_path / "kairix.sqlite",
        "envelope": None,
    }
    yield state


def _seed_doc(db_path: Path) -> None:
    db = sqlite3.connect(str(db_path))
    create_schema(db, dims=4)
    db.execute(
        "INSERT INTO documents (collection, path, hash, source_name, source_uri, "
        "source_modified_at, source_page, sensitivity, created_at, modified_at, active) "
        "VALUES ('default', 'doc.md', 'agent-alpha-mcp-1', NULL, NULL, NULL, NULL, 'public', ?, ?, 1)",
        (_NOW, _NOW),
    )
    db.commit()
    db.close()


@given("a kairix process configured with FakePaths and an MCP-callable index")
def _given_mcp_index(_mcp_state: dict[str, Any]) -> None:
    _seed_doc(_mcp_state["db_path"])
    # FakePaths used by the agent's caller in production wiring; we
    # pass db_path directly into the tool below, so this is just the
    # contract that paths are explicit (F2-clean).
    _ = FakePaths(
        db_path=_mcp_state["db_path"],
        document_root=_mcp_state["db_path"].parent / "vault",
    )


@given("a kairix index path that cannot be opened")
def _given_bogus_path(_mcp_state: dict[str, Any], tmp_path: Path) -> None:
    bogus = tmp_path / "is-a-directory"
    bogus.mkdir()
    _mcp_state["db_path"] = bogus


@when("the agent calls tool_maintenance_analyze with a tmp db_path")
def _when_agent_calls_tool(_mcp_state: dict[str, Any]) -> None:
    _mcp_state["envelope"] = tool_maintenance_analyze(db_path=_mcp_state["db_path"])


@when("the agent calls tool_maintenance_analyze on the unreachable path")
def _when_agent_calls_tool_bogus(_mcp_state: dict[str, Any]) -> None:
    _mcp_state["envelope"] = tool_maintenance_analyze(db_path=_mcp_state["db_path"])


@then("the envelope reports analyze_ran true with a non-empty reason")
def _then_envelope_ran(_mcp_state: dict[str, Any]) -> None:
    """Sabotage: replace the tool body with a static envelope returning
    analyze_ran=False — this assertion fires."""
    env = _mcp_state["envelope"]
    assert env["analyze_ran"] is True, f"expected analyze_ran=True; got {env!r}"
    assert env["reason"], f"reason should be non-empty; got {env['reason']!r}"
    assert env["error"] == ""


@then("the envelope carries rows_analyzed elapsed_ms and plan samples")
def _then_envelope_contract(_mcp_state: dict[str, Any]) -> None:
    """Sabotage: drop ``plan_after`` from the envelope and this fires."""
    env = _mcp_state["envelope"]
    for key in ("rows_analyzed", "elapsed_ms", "plan_before", "plan_after", "sample_query"):
        assert key in env, f"envelope missing {key!r}; got {sorted(env.keys())}"


@then("the envelope reports an error and analyze_ran false")
def _then_envelope_error(_mcp_state: dict[str, Any]) -> None:
    env = _mcp_state["envelope"]
    assert env["analyze_ran"] is False
    assert env["error"] != "", f"expected non-empty error; got {env!r}"
