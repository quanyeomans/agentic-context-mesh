"""Tests for ``latency_ms`` envelope injection in ``async_tool_handler``.

Issue #405 — every MCP tool envelope must surface ``latency_ms`` so agents
reading the tool response see the per-call wall-clock inline, without
needing external wall-clock measurement. ``tool_search`` was the
reference shape; this test pins the wrapper-driven injection that
spreads the same field name to every other tool's envelope.

The wrapper measures wall-clock via ``time.monotonic()`` (already used
for the ``mcp_call_log`` row) and ``setdefault``s the integer-ms value
into the result dict. ``setdefault`` preserves any pre-existing
``latency_ms`` (e.g. search's float from the use case) while adding the
field to every tool that didn't already emit one.

Sabotage-proof: each test below was first run with the
``result.setdefault("latency_ms", elapsed_ms)`` line removed from
``errors.py`` and confirmed to fail; the line was restored and the
tests pass.

Tested through the public surface (``async_tool_handler`` +
``AsyncToolHandlerDeps``); no monkeypatch, no env vars, no private
symbols (F1/F2/F5 clean).
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from kairix.agents.mcp.errors import AsyncToolHandlerDeps, async_tool_handler
from kairix.core.db.schema import create_schema

pytestmark = pytest.mark.unit


@pytest.fixture
def tmp_db(tmp_path: Path) -> Iterator[Path]:
    """Tmp-path SQLite seeded with the kairix schema (so the call-log write succeeds)."""
    db_path = tmp_path / "index.sqlite"
    conn = sqlite3.connect(str(db_path))
    create_schema(conn)
    conn.close()
    yield db_path


def test_success_envelope_has_latency_ms_as_int(tmp_db: Path) -> None:
    """A handler returning a dict without ``latency_ms`` has it injected as int.

    Sabotage proof: removed ``result.setdefault("latency_ms", elapsed_ms)``
    from ``errors.py`` — this test fails (KeyError on ``result["latency_ms"]``).
    Restored.
    """

    def my_tool(query: str) -> dict[str, str]:
        return {"q": query}

    wrapped = async_tool_handler(my_tool, deps=AsyncToolHandlerDeps(db_path_fn=lambda: tmp_db))
    result = asyncio.run(wrapped(query="ping"))

    assert "latency_ms" in result, f"expected latency_ms in envelope; got keys: {list(result)}"
    assert isinstance(result["latency_ms"], int), (
        f"latency_ms must be int (matches mcp_call_log INTEGER column); "
        f"got {type(result['latency_ms']).__name__}: {result['latency_ms']!r}"
    )
    assert result["latency_ms"] >= 0, f"latency_ms must be non-negative; got {result['latency_ms']}"


def test_latency_reflects_handler_wall_clock(tmp_db: Path) -> None:
    """A 100 ms handler produces a ``latency_ms`` in the 90-500 ms window.

    Bounds chosen to avoid CI flakiness: lower bound 90 ms allows for
    ``int()`` truncation of values like 99.7 ms; upper bound 500 ms
    swallows scheduler / threadpool startup overhead on slow runners.

    Sabotage proof: removed ``result.setdefault("latency_ms", elapsed_ms)``
    from ``errors.py`` — this test fails (KeyError). Restored.
    """

    def slow_tool() -> dict[str, str]:
        time.sleep(0.1)
        return {"ok": "yes"}

    wrapped = async_tool_handler(slow_tool, deps=AsyncToolHandlerDeps(db_path_fn=lambda: tmp_db))
    result = asyncio.run(wrapped())

    assert result["latency_ms"] >= 90, f"latency_ms should reflect ~100 ms handler sleep; got {result['latency_ms']}"
    assert result["latency_ms"] <= 500, (
        f"latency_ms should not exceed 500 ms for a 100 ms handler; got {result['latency_ms']}"
    )


def test_error_envelope_also_has_latency_ms(tmp_db: Path) -> None:
    """A handler that raises produces an error envelope WITH ``latency_ms``.

    ``wrap_tool_errors`` converts the exception to
    ``{"error": "<Class>: <msg>"}``; the latency-injection must apply to
    this branch too so agents can see how long a failing call took.

    Sabotage proof: removed ``result.setdefault("latency_ms", elapsed_ms)``
    from ``errors.py`` — this test fails (KeyError). Restored.
    """

    def boom(_q: str) -> dict[str, str]:
        raise ValueError("bad")

    wrapped = async_tool_handler(boom, deps=AsyncToolHandlerDeps(db_path_fn=lambda: tmp_db))
    result = asyncio.run(wrapped("anything"))

    assert result["error"] == "ValueError: bad"
    assert "latency_ms" in result, f"expected latency_ms in error envelope too; got keys: {list(result)}"
    assert isinstance(result["latency_ms"], int)
    assert result["latency_ms"] >= 0


def test_preexisting_latency_ms_is_preserved(tmp_db: Path) -> None:
    """A handler that already emits ``latency_ms`` (e.g. search) keeps its value.

    Search's use case publishes its own ``latency_ms`` (float ms from
    the pipeline). The wrapper must not clobber it — ``setdefault``
    semantics: present key is left alone.

    Sabotage proof: changed ``setdefault`` to plain assignment
    (``result["latency_ms"] = elapsed_ms``) — this test fails (the
    handler's 123.4 float is overwritten by the wrapper's int).
    Restored.
    """

    def search_like_tool() -> dict[str, float]:
        # Float, matches `tool_search`'s shape from search_output_to_envelope.
        return {"latency_ms": 123.4}

    wrapped = async_tool_handler(search_like_tool, deps=AsyncToolHandlerDeps(db_path_fn=lambda: tmp_db))
    result = asyncio.run(wrapped())

    assert result["latency_ms"] == 123.4, f"pre-existing latency_ms must be preserved; got {result['latency_ms']!r}"
