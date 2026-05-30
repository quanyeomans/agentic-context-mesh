"""Step definitions for mcp_dead_letter_status.feature.

Drives the MCP tool handler ``kairix.agents.mcp.server.tool_dead_letter_status``
directly with the ``read_db_path`` DI seam — no monkeypatching, no env
vars.

F1-clean: no @patch. F2-clean: no env-var manipulation. The MCP
handler is the unit under test; the DI seam keeps the test honest
about the production path.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, then, when

from kairix.agents.mcp.server import tool_dead_letter_status
from kairix.core.db.schema import create_schema

pytestmark = pytest.mark.bdd


def _seed_db(tmp_path: Path, *, with_rows: bool) -> Path:
    db_path = tmp_path / "kairix.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    if with_rows:
        db.execute(
            "INSERT INTO connector_deadletter "
            "(source_name, item_id, failure_count, last_error, last_attempt) "
            "VALUES (?, ?, ?, ?, ?)",
            ("connector-alpha", "item-1", 3, "MissingDependencyException", "2026-05-26T05:58:00Z"),
        )
        db.execute(
            "INSERT INTO bronze_records (source_name, item_id, raw_path, mime, fetched_at) VALUES (?, ?, ?, ?, ?)",
            ("connector-alpha", "item-1", "/tmp/agent-alpha/item-1.pdf", "application/pdf", "2026-05-26T05:50:00Z"),
        )
    db.commit()
    db.close()
    return db_path


@pytest.fixture
def _mcp_dl_state(tmp_path: Path) -> dict[str, Any]:
    return {
        "tmp_path": tmp_path,
        "db_path": None,
        "envelope": None,
    }


@given("an MCP-bound kairix database with no dead-letter rows")
def _fresh_db(_mcp_dl_state: dict[str, Any]) -> None:
    _mcp_dl_state["db_path"] = _seed_db(_mcp_dl_state["tmp_path"], with_rows=False)


@given("an MCP-bound kairix database seeded with dead-letter rows for one connector")
def _seeded_db(_mcp_dl_state: dict[str, Any]) -> None:
    _mcp_dl_state["db_path"] = _seed_db(_mcp_dl_state["tmp_path"], with_rows=True)


@when("the agent calls the tool_dead_letter_status MCP tool")
def _call_tool(_mcp_dl_state: dict[str, Any]) -> None:
    db_path = _mcp_dl_state["db_path"]
    _mcp_dl_state["envelope"] = tool_dead_letter_status(read_db_path=lambda: db_path)


@then("the MCP envelope has total zero and an empty per_source list")
def _envelope_zero(_mcp_dl_state: dict[str, Any]) -> None:
    env = _mcp_dl_state["envelope"]
    assert env["total"] == 0, f"expected total=0; got {env}"
    assert env["per_source"] == [], f"expected empty per_source; got {env}"
    assert env["error"] == "", f"expected no error; got {env['error']!r}"


@then("the MCP envelope has total greater than zero and a non-empty per_source list")
def _envelope_populated(_mcp_dl_state: dict[str, Any]) -> None:
    env = _mcp_dl_state["envelope"]
    assert env["total"] > 0, f"expected total>0; got {env}"
    assert len(env["per_source"]) >= 1, f"expected per_source >= 1; got {env}"


@then("the per_source entry exposes failure_count, failure_class, and mime buckets")
def _envelope_keys(_mcp_dl_state: dict[str, Any]) -> None:
    src = _mcp_dl_state["envelope"]["per_source"][0]
    for key in ("source_name", "count", "by_failure_count", "by_failure_class", "by_mime_top10", "oldest_5"):
        assert key in src, f"missing key {key!r}; got: {list(src)}"
