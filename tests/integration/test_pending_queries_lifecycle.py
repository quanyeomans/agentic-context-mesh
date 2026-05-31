"""ADR-029 G.1 — pending_queries end-to-end lifecycle.

Exercises the dispatch + carry-along surfaces against a real
tmp-path SQLite, asserting three observable outcomes:

1. A fast call returns synchronously with the real result.
2. A slow call returns the plain-text 'Processing your request...'
   string and the background thread completes the row.
3. A second call from the same agent prepends the prior completed
   result as a carry-along block.

F47 — wires through ``kairix.core.queue.dispatch.configure(...)`` with
a tmp-path-backed connection, plus the real schema bootstrap. No
@patch on kairix internals.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from kairix.core.db.schema import create_schema
from kairix.core.queue import dispatch as queue_dispatch
from kairix.core.queue.carry_along import carry_along_prefix
from kairix.core.queue.dispatch import dispatch_or_queue, reset_for_tests

pytestmark = pytest.mark.integration


@pytest.fixture
def wired_queue(tmp_path: Path) -> sqlite3.Connection:
    """Bootstrap the schema, wire the queue module, hand back a read connection."""
    db_file = tmp_path / "pending.sqlite"
    bootstrap = sqlite3.connect(str(db_file))
    create_schema(bootstrap)
    bootstrap.close()

    def _connect() -> sqlite3.Connection:
        return sqlite3.connect(str(db_file), check_same_thread=False)

    queue_dispatch.configure(db_connect=_connect)
    reader = _connect()
    yield reader
    reader.close()
    reset_for_tests()


def test_fast_call_returns_synchronously(wired_queue: sqlite3.Connection) -> None:
    """A handler that returns inside the budget yields its real value."""

    @dispatch_or_queue(budget_seconds=2.0, tool_name="tool_search")
    def fake_search(query: str, *, agent_id: str) -> dict[str, object]:
        return {"query": query, "results": [{"path": "doc1.md"}]}

    result = fake_search("kairix architecture", agent_id="agent-fast")

    assert isinstance(result, dict)
    assert result["query"] == "kairix architecture"
    assert result["results"] == [{"path": "doc1.md"}]

    # Row exists and is delivered (sync path); carry-along sees nothing.
    # F63-bounded: tmp-path DB; the test writes exactly one row.
    row = wired_queue.execute(
        "SELECT status FROM pending_queries WHERE agent_id = ? LIMIT 1",
        ("agent-fast",),
    ).fetchone()
    assert row is not None
    assert row[0] == "delivered"
    assert carry_along_prefix("agent-fast", wired_queue) == ""


def test_slow_call_returns_processing_text_then_carries_on_next_call(
    wired_queue: sqlite3.Connection,
) -> None:
    """The 'Processing...' string surfaces on call 1; call 2 carries the result back."""

    @dispatch_or_queue(budget_seconds=0.2, tool_name="tool_search")
    def slow_search(query: str, *, agent_id: str) -> dict[str, object]:
        time.sleep(0.6)
        return {"query": query, "results": [{"path": "doc-slow.md"}]}

    first = slow_search("slow query", agent_id="agent-slow")

    assert isinstance(first, str)
    assert first.startswith("Processing your request (id: q_")
    assert first.endswith("Your answer will be delivered when ready.")
    # NOT an error envelope — agents must read this as "accepted".
    assert "error" not in first.lower()

    # Wait for the background handler to complete.
    deadline = time.time() + 3.0
    while time.time() < deadline:
        status = wired_queue.execute(
            "SELECT status FROM pending_queries WHERE agent_id = ?",
            ("agent-slow",),
        ).fetchone()
        if status and status[0] == "completed":
            break
        time.sleep(0.1)

    # Now the agent makes its next call; carry-along surfaces the prior result.
    prefix = carry_along_prefix("agent-slow", wired_queue)
    assert "Earlier results now available:" in prefix
    assert "tool_search" in prefix
    assert "slow query" in prefix

    # And the row is now 'delivered' so a third call doesn't re-carry.
    follow_up = carry_along_prefix("agent-slow", wired_queue)
    assert follow_up == ""


def test_two_agents_do_not_cross_carry(wired_queue: sqlite3.Connection) -> None:
    """Agent A's completed results never carry into agent B's next call."""

    @dispatch_or_queue(budget_seconds=0.1, tool_name="tool_search")
    def slow_per_agent(query: str, *, agent_id: str) -> dict[str, object]:
        time.sleep(0.3)
        return {"query": query, "results": [], "owner": agent_id}

    slow_per_agent("a-query", agent_id="agent-iso-a")
    slow_per_agent("b-query", agent_id="agent-iso-b")

    deadline = time.time() + 2.0
    while time.time() < deadline:
        completed = wired_queue.execute(
            "SELECT COUNT(*) FROM pending_queries WHERE status = 'completed'",
        ).fetchone()[0]
        if completed >= 2:
            break
        time.sleep(0.1)

    prefix_a = carry_along_prefix("agent-iso-a", wired_queue)
    prefix_b = carry_along_prefix("agent-iso-b", wired_queue)

    assert "a-query" in prefix_a
    assert "b-query" not in prefix_a
    assert "b-query" in prefix_b
    assert "a-query" not in prefix_b
