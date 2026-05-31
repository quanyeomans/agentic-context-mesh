"""Contract tests for :func:`kairix.core.queue.dispatch.dispatch_or_queue`.

Pin three properties of the ADR-029 G.1 decorator:

1. **Budget honoured** — a handler that returns within ``budget_seconds``
   surfaces its real return value (not the queued plain-text).
2. **Budget exceeded** — a slow handler returns the plain-text
   ``"Processing your request (id: q_<hash>). Your answer will be
   delivered when ready."`` and the background future continues.
3. **Dedup window** — a second call with identical (agent_id, args)
   within 60s reuses the existing in-flight row instead of submitting
   a duplicate job.

Each test injects a tmp-file SQLite connection via
:func:`kairix.core.queue.dispatch.configure` so the suite never touches
the production DB. The schema bootstrap runs on the same connection.

F1/F2 clean: no @patch, no monkeypatch on kairix internals, no
KAIRIX_* env-var manipulation.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from kairix.core.db.schema import create_schema
from kairix.core.queue import dispatch as queue_dispatch
from kairix.core.queue.dispatch import (
    PROCESSING_TEMPLATE,
    dispatch_or_queue,
    reset_for_tests,
)

pytestmark = pytest.mark.contract


@pytest.fixture
def queue_db(tmp_path: Path) -> sqlite3.Connection:
    """Build a fresh SQLite DB with the kairix schema applied, wire it
    into the queue module, and tear down between tests.
    """
    db_file = tmp_path / "queue.sqlite"
    bootstrap = sqlite3.connect(str(db_file))
    create_schema(bootstrap)
    bootstrap.close()

    def _connect() -> sqlite3.Connection:
        return sqlite3.connect(str(db_file), check_same_thread=False)

    queue_dispatch.configure(db_connect=_connect)
    conn = _connect()
    yield conn
    conn.close()
    reset_for_tests()


def test_handler_within_budget_returns_real_value(queue_db: sqlite3.Connection) -> None:
    """Handler that returns in < budget_seconds surfaces the real result."""
    calls: list[int] = []

    @dispatch_or_queue(budget_seconds=2.0)
    def fast_handler(*, agent_id: str) -> dict[str, str]:
        calls.append(1)
        return {"answer": "fast"}

    result = fast_handler(agent_id="agent-alpha")

    assert result == {"answer": "fast"}
    assert len(calls) == 1
    # The row must exist as delivered so carry-along never re-delivers
    # a sync result.
    rows = queue_db.execute(
        "SELECT status FROM pending_queries WHERE agent_id = ?",
        ("agent-alpha",),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "delivered"


def test_handler_exceeds_budget_returns_processing_text(queue_db: sqlite3.Connection) -> None:
    """A slow handler returns plain-text 'Processing your request...' (NOT an error envelope)."""
    started = []
    release = []

    @dispatch_or_queue(budget_seconds=0.2)
    def slow_handler(*, agent_id: str) -> dict[str, str]:
        started.append(time.time())
        # Sleep past the budget. The decorator will time out the wait
        # but the background thread keeps running.
        time.sleep(0.5)
        release.append(time.time())
        return {"answer": "slow"}

    result = slow_handler(agent_id="agent-beta")

    assert isinstance(result, str), "queued path must return plain text"
    assert result.startswith("Processing your request (id: q_"), f"got: {result!r}"
    assert "Your answer will be delivered when ready." in result
    # The result string MUST NOT be a JSON error envelope — that
    # would trigger the agent's fault-tolerance heuristic.
    assert not result.startswith("{"), "queued response must be plain text, not JSON"

    # Row exists in in_progress immediately.
    rows = queue_db.execute(
        "SELECT status FROM pending_queries WHERE agent_id = ?",
        ("agent-beta",),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "in_progress"

    # Give the background thread time to complete.
    time.sleep(0.6)

    # Re-read — the row should now be 'completed' (worker thread
    # finalisation callback fired).
    rows = queue_db.execute(
        "SELECT status FROM pending_queries WHERE agent_id = ?",
        ("agent-beta",),
    ).fetchall()
    assert rows[0][0] == "completed"


def test_dedup_within_window_returns_existing_processing_text(queue_db: sqlite3.Connection) -> None:
    """A second identical call within 60s reuses the in-flight row's id."""

    @dispatch_or_queue(budget_seconds=0.1)
    def repeat_handler(*, agent_id: str) -> dict[str, str]:
        time.sleep(0.4)
        return {"answer": "slow"}

    first = repeat_handler(agent_id="agent-gamma")
    second = repeat_handler(agent_id="agent-gamma")

    assert isinstance(first, str)
    assert isinstance(second, str)
    # Both reference the same query id within the dedup window.
    first_id = first.split("(id: ")[1].split(")")[0]
    second_id = second.split("(id: ")[1].split(")")[0]
    assert first_id == second_id, "dedup must reuse the in-flight row id"

    # Exactly one row exists for the agent_id within the window.
    count = queue_db.execute(
        "SELECT COUNT(*) FROM pending_queries WHERE agent_id = ?",
        ("agent-gamma",),
    ).fetchone()[0]
    assert count == 1

    time.sleep(0.5)  # Let the background handler complete.


def test_handler_raises_within_budget_marks_failed(queue_db: sqlite3.Connection) -> None:
    """A handler that raises before the budget expires marks the row failed."""

    @dispatch_or_queue(budget_seconds=2.0)
    def raising_handler(*, agent_id: str) -> dict[str, str]:
        raise RuntimeError("intentional contract-test failure")

    with pytest.raises(RuntimeError, match="intentional contract-test failure"):
        raising_handler(agent_id="agent-delta")

    row = queue_db.execute(
        "SELECT status, error_message FROM pending_queries WHERE agent_id = ?",
        ("agent-delta",),
    ).fetchone()
    assert row[0] == "failed"
    assert "intentional contract-test failure" in row[1]


def test_processing_template_format_is_stable() -> None:
    """The plain-text template is the literal ADR-029 string."""
    rendered = PROCESSING_TEMPLATE.format(qid="q_abc123")
    assert rendered == "Processing your request (id: q_abc123). Your answer will be delivered when ready."
