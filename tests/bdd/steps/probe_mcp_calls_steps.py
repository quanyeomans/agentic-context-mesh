"""Step definitions for probe_mcp_calls.feature (#398 Workstream D).

Drives :func:`kairix.quality.probe.mcp_calls_cli.main` against a
tmp-path SQLite seeded with rows that mimic what
:func:`kairix.agents.mcp.errors._record_mcp_call` writes in production.

F1-clean (no @patch on kairix internals), F2-clean (no env
monkeypatch), F5-clean (only public-surface imports), F13-clean
(no implementation symbols leak into the feature file).

Sabotage notes per scenario (mutate prod → confirm fail → restore):

* "lists both tool names" — replace ``stats.sort(key=lambda s: s.count, ...)``
  with a no-op so the order is undefined; the per-tool name assertions
  still pass because we look for the names, but flip the sort to
  drop one tool from the output instead and the scenario fails on the
  missing name. Confirmed; restored.

* "says no calls recorded" — change the empty-window branch to return
  "all clear" instead of "no calls recorded" — the scenario fails on
  the missing phrase. Confirmed; restored.

* "JSON envelope" — change ``json.dumps`` to ``str(payload)`` (no
  longer valid JSON) — scenario fails when ``json.loads`` raises.
  Confirmed; restored.

* "rejects malformed --since" — accept any string in ``_parse_since``
  (drop the regex check) — the bad token produces an empty timestamp
  filter, the CLI exits 0, scenario fails on exit code 2.
  Confirmed; restored.
"""

from __future__ import annotations

import io
import json
import sqlite3
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from kairix.core.db.schema import create_schema
from kairix.quality.probe.mcp_calls_cli import McpCallsDeps
from kairix.quality.probe.mcp_calls_cli import main as mcp_calls_main

scenarios("../features/probe_mcp_calls.feature")


def _make_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "index.sqlite"
    conn = sqlite3.connect(str(db_path))
    create_schema(conn)
    conn.close()
    return db_path


def _seed(db_path: Path, rows: list[tuple[str, str, str | None, int, int, str | None]]) -> None:
    """Seed mcp_call_log with (timestamp, tool, agent, latency_ms, success, error_class)."""
    conn = sqlite3.connect(str(db_path))
    try:
        for ts, tool, agent, latency_ms, success, error_class in rows:
            conn.execute(
                "INSERT INTO mcp_call_log "
                "(timestamp, tool, agent, latency_ms, success, error_class, payload_hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ts, tool, agent, latency_ms, success, error_class, "hash000000000000"),
            )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def ctx(tmp_path: Path) -> dict[str, Any]:
    """Per-scenario context — DB path, stdout/stderr captures, exit code."""
    return {
        "db_path": _make_db(tmp_path),
        "stdout": "",
        "stderr": "",
        "rc": 0,
    }


@given("the mcp_call_log table has rows for two tools")
def _given_two_tools(ctx: dict[str, Any]) -> None:
    _seed(
        ctx["db_path"],
        [
            ("2026-06-03T10:00:00Z", "search", "shape", 120, 1, None),
            ("2026-06-03T10:00:01Z", "search", "shape", 250, 1, None),
            ("2026-06-03T10:00:02Z", "search", "shape", 9000, 0, "TimeoutError"),
            ("2026-06-03T10:00:03Z", "brief", "shape", 1500, 1, None),
            ("2026-06-03T10:00:04Z", "brief", "shape", 1800, 0, "RuntimeError"),
        ],
    )


@given("the mcp_call_log table has rows for one tool")
def _given_one_tool(ctx: dict[str, Any]) -> None:
    _seed(
        ctx["db_path"],
        [
            ("2026-06-03T10:00:00Z", "search", "shape", 120, 1, None),
            ("2026-06-03T10:00:01Z", "search", "shape", 200, 1, None),
        ],
    )


@given("the mcp_call_log table is empty")
def _given_empty(ctx: dict[str, Any]) -> None:
    # _make_db already created an empty table — nothing to seed.
    pass


def _run(ctx: dict[str, Any], argv: list[str]) -> None:
    """Invoke mcp_calls_main with injected deps and capture stdout/stderr/rc."""
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    db_path = ctx["db_path"]
    deps = McpCallsDeps(db_path_fn=lambda: db_path)
    with redirect_stdout(out_buf), redirect_stderr(err_buf):
        rc = mcp_calls_main(argv, deps=deps)
    ctx["stdout"] = out_buf.getvalue()
    ctx["stderr"] = err_buf.getvalue()
    ctx["rc"] = rc


@when("the operator runs probe mcp-calls")
def _when_default(ctx: dict[str, Any]) -> None:
    _run(ctx, [])


@when("the operator runs probe mcp-calls with the json flag")
def _when_json(ctx: dict[str, Any]) -> None:
    _run(ctx, ["--json"])


@when(parsers.parse("the operator runs probe mcp-calls with --since {value}"))
def _when_since(ctx: dict[str, Any], value: str) -> None:
    _run(ctx, ["--since", value])


@then("the report lists both tool names")
def _then_lists_both(ctx: dict[str, Any]) -> None:
    out = ctx["stdout"]
    assert "search" in out, f"search not found in:\n{out}"
    assert "brief" in out, f"brief not found in:\n{out}"


@then("each tool row shows count, p50, p95, p99, success rate, and top errors")
def _then_columns(ctx: dict[str, Any]) -> None:
    out = ctx["stdout"]
    for header in ("count", "p50ms", "p95ms", "p99ms", "ok%", "top_errors"):
        assert header in out, f"header {header!r} missing from report:\n{out}"


@then("the report says no calls recorded")
def _then_empty_message(ctx: dict[str, Any]) -> None:
    assert "no calls recorded" in ctx["stdout"], f"expected 'no calls recorded'; got:\n{ctx['stdout']}"


@then("stdout is a valid JSON object with a tools array")
def _then_valid_json(ctx: dict[str, Any]) -> None:
    payload = json.loads(ctx["stdout"])
    assert "tools" in payload
    assert isinstance(payload["tools"], list)
    assert len(payload["tools"]) >= 1


@then("the CLI exits with code 2")
def _then_exit_2(ctx: dict[str, Any]) -> None:
    assert ctx["rc"] == 2, f"expected exit 2; got {ctx['rc']}, stderr=\n{ctx['stderr']}"


@then("stderr names the accepted shape")
def _then_stderr_shape(ctx: dict[str, Any]) -> None:
    assert "duration" in ctx["stderr"].lower() or "s/m/h/d" in ctx["stderr"], (
        f"stderr should explain the accepted --since shape; got:\n{ctx['stderr']}"
    )
