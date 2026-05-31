"""Contract tests for :func:`kairix.core.queue.carry_along.carry_along_prefix`.

Pin three properties of the ADR-029 G.1 carry-along middleware:

1. **5-cap** — at most CARRY_ALONG_CAP completed rows are surfaced per call.
2. **Ordering** — rows return in completed_at ascending order.
3. **Mark-delivered semantics** — once carried, rows flip to
   ``status='delivered'`` so a second call doesn't re-deliver them.

The mark-delivered behaviour is the sabotage-prone path: dropping the
UPDATE breaks the dedup contract. Test
:func:`test_second_call_does_not_redeliver_completed_rows` IS the
sabotage proof — mutate the UPDATE in carry_along.py and watch this
assertion fail.

F1/F2 clean: direct SQLite tmp file; no @patch on kairix internals.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from kairix.core.db.schema import create_schema
from kairix.core.queue.carry_along import (
    CARRY_ALONG_CAP,
    carry_along_prefix,
    resolve_agent_id,
)

pytestmark = pytest.mark.contract


@pytest.fixture
def queue_db(tmp_path: Path) -> sqlite3.Connection:
    db_file = tmp_path / "carry.sqlite"
    conn = sqlite3.connect(str(db_file))
    create_schema(conn)
    yield conn
    conn.close()


def _insert_completed_row(
    db: sqlite3.Connection,
    *,
    query_id: str,
    agent_id: str,
    tool: str,
    result_payload: dict[str, object],
    completed_at: datetime,
) -> None:
    """Helper — seed a completed row for the agent."""
    submitted = (completed_at - timedelta(seconds=2)).isoformat()
    completed = completed_at.isoformat()
    db.execute(
        "INSERT INTO pending_queries "
        "(id, agent_id, tool, args_json, args_hash, status, "
        " submitted_at, started_at, completed_at, result_json) "
        "VALUES (?, ?, ?, ?, ?, 'completed', ?, ?, ?, ?)",
        (
            query_id,
            agent_id,
            tool,
            "{}",
            query_id,  # synthetic hash — uniqueness across rows
            submitted,
            submitted,
            completed,
            json.dumps(result_payload),
        ),
    )
    db.commit()


def test_no_completed_rows_returns_empty_prefix(queue_db: sqlite3.Connection) -> None:
    """When nothing is queued, carry-along is a no-op (empty string)."""
    assert carry_along_prefix("agent-alpha", queue_db) == ""


def test_returns_prefix_with_one_completed_row(queue_db: sqlite3.Connection) -> None:
    """A single completed row appears in the prefix."""
    now = datetime.now(timezone.utc)
    _insert_completed_row(
        queue_db,
        query_id="q_aaaa1111",
        agent_id="agent-alpha",
        tool="tool_search",
        result_payload={"query": "kairix", "results": [{"path": "doc1.md"}]},
        completed_at=now,
    )

    prefix = carry_along_prefix("agent-alpha", queue_db)

    assert "Earlier results now available:" in prefix
    assert "[q_aaaa1111]" in prefix
    assert "tool_search" in prefix
    assert "kairix" in prefix
    assert "results=1" in prefix


def test_caps_at_five_completed_rows(queue_db: sqlite3.Connection) -> None:
    """No matter how many completed rows exist, at most CARRY_ALONG_CAP appear."""
    now = datetime.now(timezone.utc)
    for i in range(7):
        _insert_completed_row(
            queue_db,
            query_id=f"q_cap{i:04d}",
            agent_id="agent-cap",
            tool="tool_search",
            result_payload={"query": f"q{i}", "results": []},
            completed_at=now + timedelta(seconds=i),
        )

    prefix = carry_along_prefix("agent-cap", queue_db)

    # Lines starting with "- [" enumerate carried rows.
    carried = [line for line in prefix.splitlines() if line.startswith("- [")]
    assert len(carried) == CARRY_ALONG_CAP == 5


def test_orders_by_completed_at_ascending(queue_db: sqlite3.Connection) -> None:
    """Rows surface in completion order (oldest first)."""
    base = datetime.now(timezone.utc)
    _insert_completed_row(
        queue_db,
        query_id="q_newer000",
        agent_id="agent-order",
        tool="tool_search",
        result_payload={"query": "newer", "results": []},
        completed_at=base + timedelta(seconds=5),
    )
    _insert_completed_row(
        queue_db,
        query_id="q_older000",
        agent_id="agent-order",
        tool="tool_search",
        result_payload={"query": "older", "results": []},
        completed_at=base,
    )

    prefix = carry_along_prefix("agent-order", queue_db)
    lines = [line for line in prefix.splitlines() if line.startswith("- [")]
    assert lines[0].startswith("- [q_older000]")
    assert lines[1].startswith("- [q_newer000]")


def test_second_call_does_not_redeliver_completed_rows(queue_db: sqlite3.Connection) -> None:
    """The mark-delivered UPDATE flips status so the next call doesn't re-deliver.

    Sabotage proof: if the UPDATE in carry_along.py is removed, this
    assertion (``second_prefix == ""``) fails — the same row gets
    delivered twice. Mutate, run, restore.
    """
    now = datetime.now(timezone.utc)
    _insert_completed_row(
        queue_db,
        query_id="q_dedup0000",
        agent_id="agent-dedup",
        tool="tool_search",
        result_payload={"query": "first", "results": []},
        completed_at=now,
    )

    first_prefix = carry_along_prefix("agent-dedup", queue_db)
    second_prefix = carry_along_prefix("agent-dedup", queue_db)

    assert "q_dedup0000" in first_prefix
    assert second_prefix == "", (
        "second carry-along call must NOT re-deliver — the UPDATE in "
        "carry_along.py is what makes this hold. If this assertion fails, "
        "either the UPDATE was dropped or status='delivered' isn't filtered out."
    )

    # The delivered row's status moved to 'delivered' and delivered_at is set.
    row = queue_db.execute(
        "SELECT status, delivered_at FROM pending_queries WHERE id = ?",
        ("q_dedup0000",),
    ).fetchone()
    assert row[0] == "delivered"
    assert row[1] is not None


def test_failed_rows_carry_with_error_summary(queue_db: sqlite3.Connection) -> None:
    """Failed rows surface so the agent knows the prior attempt didn't return cleanly."""
    now = datetime.now(timezone.utc).isoformat()
    queue_db.execute(
        "INSERT INTO pending_queries "
        "(id, agent_id, tool, args_json, args_hash, status, "
        " submitted_at, completed_at, error_message) "
        "VALUES ('q_fail1111', 'agent-fail', 'tool_search', '{}', 'h', 'failed', ?, ?, 'boom')",
        (now, now),
    )
    queue_db.commit()

    prefix = carry_along_prefix("agent-fail", queue_db)

    assert "[q_fail1111]" in prefix


def test_empty_agent_id_returns_empty_prefix(queue_db: sqlite3.Connection) -> None:
    """A blank agent_id never reads from the table."""
    assert carry_along_prefix("", queue_db) == ""


def test_resolve_agent_id_prefers_mcp_session_header() -> None:
    """Mcp-Session-Id wins over X-Kairix-Agent per ADR-029 §"Agent identity"."""
    agent_id, fallback = resolve_agent_id(
        {"Mcp-Session-Id": "session-1", "X-Kairix-Agent": "explicit-2"},
    )
    assert agent_id == "session-1"
    assert fallback is False


def test_resolve_agent_id_falls_back_to_x_kairix_agent() -> None:
    """When MCP session is absent, X-Kairix-Agent is the next choice."""
    agent_id, fallback = resolve_agent_id({"X-Kairix-Agent": "explicit-only"})
    assert agent_id == "explicit-only"
    assert fallback is False


def test_resolve_agent_id_falls_back_to_unknown_agent() -> None:
    """No headers at all → process-global fallback."""
    agent_id, fallback = resolve_agent_id({})
    assert agent_id == "unknown-agent"
    assert fallback is True

    agent_id_none, fallback_none = resolve_agent_id(None)
    assert agent_id_none == "unknown-agent"
    assert fallback_none is True


def test_resolve_agent_id_handles_blank_session_header() -> None:
    """An empty Mcp-Session-Id falls through to X-Kairix-Agent."""
    agent_id, fallback = resolve_agent_id({"Mcp-Session-Id": "", "X-Kairix-Agent": "explicit"})
    assert agent_id == "explicit"
    assert fallback is False


def test_resolve_agent_id_handles_blank_x_kairix_agent() -> None:
    """An empty X-Kairix-Agent header falls through to the unknown-agent fallback."""
    agent_id, fallback = resolve_agent_id({"X-Kairix-Agent": ""})
    assert agent_id == "unknown-agent"
    assert fallback is True


def test_log_agent_fallback_affordance_is_noop_when_not_fallback() -> None:
    """Logging is suppressed when the canonical headers are present."""
    from kairix.core.queue.carry_along import log_agent_fallback_affordance

    captured: list[str] = []

    class _RecordingLogger:
        def info(self, msg: str, *args: object, **kwargs: object) -> None:
            captured.append(msg)

    log_agent_fallback_affordance(False, logger_=_RecordingLogger())
    assert captured == []


def test_log_agent_fallback_affordance_emits_when_fallback_fired() -> None:
    """Logging fires (with F21 affordance markers) when the fallback was used."""
    from kairix.core.queue.carry_along import log_agent_fallback_affordance

    captured: list[str] = []

    class _RecordingLogger:
        def info(self, msg: str, *args: object, **kwargs: object) -> None:
            captured.append(msg)

    log_agent_fallback_affordance(True, logger_=_RecordingLogger())
    assert len(captured) == 1
    assert "fix:" in captured[0]
    assert "next:" in captured[0]
    assert "run:" in captured[0]


def test_log_agent_fallback_affordance_uses_module_logger_by_default() -> None:
    """When no logger is passed, the module logger is used (smoke test)."""
    from kairix.core.queue.carry_along import log_agent_fallback_affordance

    # Should not raise — module logger handles it.
    log_agent_fallback_affordance(True)


def test_format_result_summary_handles_empty_result_json(queue_db: sqlite3.Connection) -> None:
    """A NULL result_json renders as <empty>."""
    now = datetime.now(timezone.utc).isoformat()
    queue_db.execute(
        "INSERT INTO pending_queries "
        "(id, agent_id, tool, args_json, args_hash, status, "
        " submitted_at, completed_at, result_json) "
        "VALUES ('q_empty0000', 'agent-empty', 'tool_search', '{}', 'h', 'completed', ?, ?, NULL)",
        (now, now),
    )
    queue_db.commit()

    prefix = carry_along_prefix("agent-empty", queue_db)
    assert "<empty>" in prefix


def test_format_result_summary_handles_unparseable_json(queue_db: sqlite3.Connection) -> None:
    """Garbage JSON in result_json renders as <unparseable>."""
    now = datetime.now(timezone.utc).isoformat()
    queue_db.execute(
        "INSERT INTO pending_queries "
        "(id, agent_id, tool, args_json, args_hash, status, "
        " submitted_at, completed_at, result_json) "
        "VALUES ('q_garbage000', 'agent-garbage', 'tool_search', '{}', 'h', 'completed', ?, ?, 'not-json')",
        (now, now),
    )
    queue_db.commit()

    prefix = carry_along_prefix("agent-garbage", queue_db)
    assert "<unparseable>" in prefix


def test_format_result_summary_handles_generic_dict(queue_db: sqlite3.Connection) -> None:
    """A dict without query/results/error renders with its top-level keys."""
    now = datetime.now(timezone.utc).isoformat()
    payload = json.dumps({"alpha": 1, "beta": 2})
    queue_db.execute(
        "INSERT INTO pending_queries "
        "(id, agent_id, tool, args_json, args_hash, status, "
        " submitted_at, completed_at, result_json) "
        "VALUES ('q_generic000', 'agent-generic', 'tool_other', '{}', 'h', 'completed', ?, ?, ?)",
        (now, now, payload),
    )
    queue_db.commit()

    prefix = carry_along_prefix("agent-generic", queue_db)
    assert "keys=" in prefix
    assert "alpha" in prefix


def test_format_result_summary_handles_non_dict(queue_db: sqlite3.Connection) -> None:
    """A non-dict (e.g. a list) renders as its repr."""
    now = datetime.now(timezone.utc).isoformat()
    payload = json.dumps([1, 2, 3])
    queue_db.execute(
        "INSERT INTO pending_queries "
        "(id, agent_id, tool, args_json, args_hash, status, "
        " submitted_at, completed_at, result_json) "
        "VALUES ('q_list00000', 'agent-list', 'tool_other', '{}', 'h', 'completed', ?, ?, ?)",
        (now, now, payload),
    )
    queue_db.commit()

    prefix = carry_along_prefix("agent-list", queue_db)
    assert "[1, 2, 3]" in prefix


def test_carry_along_prefix_safe_returns_empty_for_none_db() -> None:
    """The production-safe wrapper short-circuits when the DB is unavailable."""
    from kairix.core.queue.carry_along import carry_along_prefix_safe

    assert carry_along_prefix_safe("agent-x", None) == ""


def test_carry_along_prefix_safe_swallows_sqlite_errors(queue_db: sqlite3.Connection) -> None:
    """A connection that raises returns the empty string + logs a warning."""
    from kairix.core.queue.carry_along import carry_along_prefix_safe

    queue_db.close()  # any subsequent execute will raise sqlite3.ProgrammingError

    # ProgrammingError is a subclass of sqlite3.Error so the production-safe
    # wrapper catches it.
    assert carry_along_prefix_safe("agent-x", queue_db) == ""


def test_carry_along_prefix_safe_returns_prefix_on_success(queue_db: sqlite3.Connection) -> None:
    """Happy path through the production-safe wrapper."""
    from kairix.core.queue.carry_along import carry_along_prefix_safe

    now = datetime.now(timezone.utc)
    _insert_completed_row(
        queue_db,
        query_id="q_safe00000",
        agent_id="agent-safe",
        tool="tool_search",
        result_payload={"query": "safe", "results": []},
        completed_at=now,
    )
    prefix = carry_along_prefix_safe("agent-safe", queue_db)
    assert "[q_safe00000]" in prefix
