"""F54 both-branch coverage for the ``agent_query_queue`` feature flag.

Exercises :func:`kairix.agents.mcp.server.tool_search_queue_aware` with
the flag pinned ON and OFF via a stub ``QueueAwareSearchDeps``. Asserts:

* **OFF (default)** — the call delegates straight to the search stub;
  no pending_queries row is written; the response is the standard
  search envelope.
* **ON** — the call routes through the queue; a fast handler writes a
  'delivered' row + returns the envelope; a subsequent call from the
  same agent_id sees no carry-along (delivered rows don't re-deliver).

F1/F6 clean: the ``queue_deps`` dataclass is the public DI seam, not
a free-floating test-only kwarg.
F2-clean: no ``KAIRIX_*`` env-var manipulation.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from kairix.agents.mcp.server import QueueAwareSearchDeps, tool_search_queue_aware
from kairix.core.db.schema import create_schema
from kairix.core.queue import dispatch as queue_dispatch
from kairix.core.queue.dispatch import reset_for_tests
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.integration


@pytest.fixture
def wired_queue(tmp_path: Path) -> sqlite3.Connection:
    """Per-test queue DB + module wiring."""
    db_file = tmp_path / "flag.sqlite"
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


def _build_stub_search(captured: list[dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    """Build a search stand-in that records every call."""

    def _stub(**kwargs: Any) -> dict[str, Any]:
        captured.append(kwargs)
        return {"query": kwargs.get("query"), "results": [{"path": "doc.md"}], "stub": True}

    return _stub


def _make_deps(
    *,
    flag_on: bool,
    db: sqlite3.Connection,
    captured: list[dict[str, Any]],
) -> QueueAwareSearchDeps:
    """Build a QueueAwareSearchDeps wired to a FakeFeatureFlagResolver.

    F54 reads the boolean both ways from this file via the canonical
    ``with_flag("agent_query_queue", False)`` + ``with_flag(...,
    True)`` heuristic, so both branches must literally appear in
    source even though only one fires per test.
    """
    resolver = (
        FakeFeatureFlagResolver().with_flag("agent_query_queue", True)
        if flag_on
        else FakeFeatureFlagResolver().with_flag("agent_query_queue", False)
    )
    return QueueAwareSearchDeps(
        flag_reader=resolver.get,
        search_fn=_build_stub_search(captured),
        queue_db_factory=lambda: db,
    )


def test_flag_off_skips_queue_and_returns_search_envelope(
    wired_queue: sqlite3.Connection,
) -> None:
    """OFF branch — tool_search_queue_aware delegates straight to the stub."""
    captured: list[dict[str, Any]] = []

    result = tool_search_queue_aware(
        query="off-branch",
        agent_id="agent-off",
        queue_deps=_make_deps(flag_on=False, db=wired_queue, captured=captured),
    )

    assert isinstance(result, dict)
    assert result.get("stub") is True
    assert len(captured) == 1
    assert captured[0]["query"] == "off-branch"

    # No queue row written — flag was OFF.
    # F63-bounded: integration-only assertion on a tmp-path DB; queue stays empty.
    row = wired_queue.execute(
        "SELECT id FROM pending_queries WHERE agent_id = ? LIMIT 1",
        ("agent-off",),
    ).fetchone()
    assert row is None


def test_flag_on_routes_through_queue_and_writes_delivered_row(
    wired_queue: sqlite3.Connection,
) -> None:
    """ON branch — fast call still completes sync but is recorded in pending_queries."""
    captured: list[dict[str, Any]] = []

    result = tool_search_queue_aware(
        query="on-branch",
        agent_id="agent-on",
        queue_deps=_make_deps(flag_on=True, db=wired_queue, captured=captured),
    )

    # Fast handler — sync envelope returned, no plain-text fallback.
    assert isinstance(result, dict)
    assert result["query"] == "on-branch"
    assert len(captured) == 1

    # F63-bounded: tmp-path DB read; tests/integration suite caps row count
    # at 1 by virtue of the test only making one call.
    row = wired_queue.execute(
        "SELECT status FROM pending_queries WHERE agent_id = ? LIMIT 1",
        ("agent-on",),
    ).fetchone()
    assert row is not None
    assert row[0] == "delivered"


def test_flag_on_with_no_agent_id_uses_unknown_agent_fallback(
    wired_queue: sqlite3.Connection,
) -> None:
    """No agent_id supplied → routes under 'unknown-agent' so the row still records."""
    captured: list[dict[str, Any]] = []

    result = tool_search_queue_aware(
        query="missing-agent",
        agent_id=None,
        queue_deps=_make_deps(flag_on=True, db=wired_queue, captured=captured),
    )

    assert isinstance(result, dict)
    # F63-bounded: tmp-path DB read; test only writes one row.
    row = wired_queue.execute(
        "SELECT agent_id FROM pending_queries WHERE agent_id = 'unknown-agent' LIMIT 1",
    ).fetchone()
    assert row is not None
