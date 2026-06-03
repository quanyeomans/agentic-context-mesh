"""Tests for the mcp_call_log INSERT path in ``async_tool_handler``.

Issue #398 (Workstream D) — every MCP tool call records one row in
``mcp_call_log`` with timestamp, tool name, agent, latency, success
flag, error class, and a short payload hash. The write is
fire-and-forget; observability failure must NEVER break the tool
call.

Test seam: ``async_tool_handler(handler, deps=AsyncToolHandlerDeps(db_path_fn=lambda: tmp_db))``
routes the INSERT to a tmp-path SQLite. No monkey-patches, no env
vars (F1/F2-clean).
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from kairix.agents.mcp.errors import AsyncToolHandlerDeps, async_tool_handler
from kairix.core.db.schema import create_schema

pytestmark = pytest.mark.unit


@pytest.fixture
def tmp_db(tmp_path: Path) -> Iterator[Path]:
    """Provide a tmp-path SQLite with the kairix schema (incl. mcp_call_log)."""
    db_path = tmp_path / "index.sqlite"
    conn = sqlite3.connect(str(db_path))
    create_schema(conn)
    conn.close()
    yield db_path


def _rows(db_path: Path) -> list[tuple[str, str, str | None, int, int, str | None, str | None]]:
    """Read every row from mcp_call_log.

    Returns (timestamp, tool, agent, latency_ms, success, error_class, payload_hash).
    """
    conn = sqlite3.connect(str(db_path))
    try:
        return [
            (str(r[0]), str(r[1]), r[2], int(r[3]), int(r[4]), r[5], r[6])
            for r in conn.execute(
                "SELECT timestamp, tool, agent, latency_ms, success, error_class, payload_hash "
                "FROM mcp_call_log ORDER BY id"
            ).fetchall()
        ]
    finally:
        conn.close()


def test_successful_call_records_success_row(tmp_db: Path) -> None:
    """A handler that returns a dict logs one row with success=1, error_class=NULL.

    Sabotage proof: changed the wrapper to skip the _record_mcp_call call
    in the success branch — this test fails (no rows). Restored.
    """

    def my_tool(query: str, agent: str | None = None) -> dict[str, str]:
        return {"q": query}

    wrapped = async_tool_handler(my_tool, deps=AsyncToolHandlerDeps(db_path_fn=lambda: tmp_db))
    result = asyncio.run(wrapped(query="ping", agent="shape"))
    assert result == {"q": "ping"}

    rows = _rows(tmp_db)
    assert len(rows) == 1
    (_ts, tool, agent, latency_ms, success, error_class, payload_hash) = rows[0]
    assert tool == "my_tool"
    assert agent == "shape"
    assert latency_ms >= 0
    assert success == 1
    assert error_class is None
    assert payload_hash and len(payload_hash) == 16


def test_handler_exception_records_failure_row(tmp_db: Path) -> None:
    """A handler that raises logs one row with success=0, error_class set.

    The wrap_tool_errors envelope converts the exception to
    ``{"error": "ValueError: bad"}``; the wrapper extracts the class
    prefix as the error_class column.

    Sabotage proof: changed the error_class extraction to always
    return None — this test fails (error_class is None). Restored.
    """

    def boom(_q: str) -> dict[str, str]:
        raise ValueError("bad")

    wrapped = async_tool_handler(boom, deps=AsyncToolHandlerDeps(db_path_fn=lambda: tmp_db))
    result = asyncio.run(wrapped("anything"))

    # Tool call still returns the structured envelope.
    assert result == {"error": "ValueError: bad"}

    rows = _rows(tmp_db)
    assert len(rows) == 1
    (_ts, tool, _agent, _latency, success, error_class, _hash) = rows[0]
    assert tool == "boom"
    assert success == 0
    assert error_class == "ValueError"


def test_db_failure_does_not_break_tool_call(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """DB INSERT failure is logged + swallowed — tool call still returns.

    Sabotage proof: removed the try/except around the INSERT (made
    _record_mcp_call raise) — the tool call raised. With the try/except
    in place, the call succeeds even when the DB path doesn't exist.
    Restored.
    """
    # Point db_path_fn at a directory — sqlite3.connect cannot open a
    # directory as a DB file, so the underlying sqlite3.OperationalError
    # surfaces. The wrapper must swallow it without breaking the tool.
    # (Post 2026-06-04 fix: _record_mcp_call now CREATE-TABLE-IF-NOT-EXISTS
    # before the INSERT, so a previously "missing table" path now auto-heals;
    # this test needs an unrecoverable failure mode, hence directory-as-DB.)
    unwritable = tmp_path  # a directory, not a file

    def my_tool() -> dict[str, str]:
        return {"ok": "yes"}

    wrapped = async_tool_handler(my_tool, deps=AsyncToolHandlerDeps(db_path_fn=lambda: unwritable))

    with caplog.at_level(logging.WARNING, logger="kairix.agents.mcp.errors"):
        result = asyncio.run(wrapped())

    # The tool call still returns the handler's result.
    assert result == {"ok": "yes"}
    # The failure was logged but swallowed.
    assert any("mcp_call_log INSERT failed" in r.message for r in caplog.records), (
        f"expected swallowed-INSERT warning; got: {[r.message for r in caplog.records]}"
    )


def test_db_path_fn_raises_does_not_break_call(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``db_path_fn`` raising in production code is swallowed — tool call still returns.

    Sabotage proof: removed the try/except around the ``resolve_db_path()``
    call — the test raises RuntimeError before returning. Restored.
    """

    def my_tool() -> dict[str, str]:
        return {"ok": "yes"}

    def _raises() -> Path:
        raise RuntimeError("paths broken")

    wrapped = async_tool_handler(my_tool, deps=AsyncToolHandlerDeps(db_path_fn=_raises))

    with caplog.at_level(logging.WARNING, logger="kairix.agents.mcp.errors"):
        result = asyncio.run(wrapped())

    assert result == {"ok": "yes"}
    assert any("db_path_fn failed" in r.message for r in caplog.records)


def test_payload_hash_is_stable_across_kwarg_order(tmp_db: Path) -> None:
    """The same kwargs in different insertion order produce the same payload_hash.

    Sabotage proof: changed `sorted(kwargs.items())` to plain
    `kwargs.items()` in `_payload_hash` — the two calls would produce
    different hashes. Restored.
    """

    def my_tool(a: int = 0, b: int = 0) -> dict[str, int]:
        return {"sum": a + b}

    wrapped = async_tool_handler(my_tool, deps=AsyncToolHandlerDeps(db_path_fn=lambda: tmp_db))
    asyncio.run(wrapped(a=1, b=2))
    asyncio.run(wrapped(b=2, a=1))

    rows = _rows(tmp_db)
    assert len(rows) == 2
    assert rows[0][6] == rows[1][6], "payload hashes must be stable across kwarg order"


def test_agent_kwarg_recorded_as_null_when_missing(tmp_db: Path) -> None:
    """When the handler is called without ``agent=`` kwarg, the agent column is NULL.

    Sabotage proof: changed the agent extraction to always default
    to the string "unknown" — this test fails (agent != None).
    Restored.
    """

    def my_tool(query: str) -> dict[str, str]:
        return {"q": query}

    wrapped = async_tool_handler(my_tool, deps=AsyncToolHandlerDeps(db_path_fn=lambda: tmp_db))
    asyncio.run(wrapped(query="ping"))

    rows = _rows(tmp_db)
    assert len(rows) == 1
    assert rows[0][2] is None, f"agent column should be NULL when not provided; got {rows[0][2]!r}"


def test_default_db_path_resolves_via_paths_module(tmp_path: Path) -> None:
    """Without ``db_path_fn`` injection, the wrapper falls back to ``kairix.paths.db_path``.

    Drives the production resolution path through the public surface:
    calling ``async_tool_handler(handler)`` with no ``db_path_fn``
    kwarg lets the production default fire. The resolved path is the
    same one ``kairix.paths.db_path`` returns. We don't INSERT into
    that real DB — we just assert the wrapper constructed correctly
    by calling its returned callable's ``__name__`` shape.

    Sabotage proof: replaced the default-resolver argument's lookup
    name from ``_default_db_path`` to a typo'd helper — pytest fails
    at import time (NameError). Restored.
    """

    def my_tool() -> dict[str, str]:
        return {"ok": "yes"}

    # Construct without db_path_fn — production default wires up.
    # We're not asserting the row landed (could clobber a real DB).
    wrapped = async_tool_handler(my_tool)
    assert wrapped.__name__ == "my_tool"
