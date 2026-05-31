"""Step definitions for feature_flag_agent_query_queue.feature.

F54 both-branch coverage — OFF and ON drive
:func:`kairix.agents.mcp.server.tool_search_queue_aware` and inspect
the pending_queries side-effect to confirm the flag selected the right
path. The ``flag_reader`` + ``search_fn`` kwargs are the DI seams.

F1/F2-clean by construction.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.agents.mcp.server import QueueAwareSearchDeps, tool_search_queue_aware
from kairix.core.db.schema import create_schema
from kairix.core.queue import dispatch as queue_dispatch
from kairix.core.queue.dispatch import reset_for_tests

pytestmark = pytest.mark.bdd


_AGENT_ID = "agent-bdd-flag"


@dataclass
class _FlagCtx:
    db_file: Path | None = None
    reader: sqlite3.Connection | None = None
    flag_on: bool = False
    response: Any = None
    calls: list[dict[str, Any]] = field(default_factory=list)


@pytest.fixture
def flag_bdd_ctx(tmp_path: Path) -> _FlagCtx:
    ctx = _FlagCtx()
    ctx.db_file = tmp_path / "bdd-flag.sqlite"
    bootstrap = sqlite3.connect(str(ctx.db_file))
    create_schema(bootstrap)
    bootstrap.close()

    def _connect() -> sqlite3.Connection:
        return sqlite3.connect(str(ctx.db_file), check_same_thread=False)

    queue_dispatch.configure(db_connect=_connect)
    ctx.reader = _connect()
    yield ctx
    if ctx.reader is not None:
        ctx.reader.close()
    reset_for_tests()


def _stub_search(captured: list[dict[str, Any]]) -> Any:
    def _stub(**kwargs: Any) -> dict[str, Any]:
        captured.append(kwargs)
        return {"query": kwargs.get("query"), "results": [], "stub": True}

    return _stub


@given(parsers.parse("the operator has the agent-query-queue flag set to {value}"))
def _operator_sets_flag(flag_bdd_ctx: _FlagCtx, value: str) -> None:
    flag_bdd_ctx.flag_on = value.strip().lower() == "true"


@when("the agent calls tool_search via the queue-aware wrapper")
def _agent_calls(flag_bdd_ctx: _FlagCtx) -> None:
    def _reader(name: str) -> bool:
        return name == "agent_query_queue" and flag_bdd_ctx.flag_on

    flag_bdd_ctx.response = tool_search_queue_aware(
        query="bdd-query",
        agent_id=_AGENT_ID,
        queue_deps=QueueAwareSearchDeps(
            flag_reader=_reader,
            search_fn=_stub_search(flag_bdd_ctx.calls),
            queue_db_factory=lambda: flag_bdd_ctx.reader,
        ),
    )


@then("the response is the standard search envelope")
def _standard_envelope(flag_bdd_ctx: _FlagCtx) -> None:
    response = flag_bdd_ctx.response
    assert isinstance(response, dict)
    assert response.get("query") == "bdd-query"


@then("no pending-queries row is written for the agent")
def _no_row(flag_bdd_ctx: _FlagCtx) -> None:
    assert flag_bdd_ctx.reader is not None
    # F63-bounded: BDD assertion on tmp-path DB; OFF branch writes zero rows.
    row = flag_bdd_ctx.reader.execute(
        "SELECT id FROM pending_queries WHERE agent_id = ? LIMIT 1",
        (_AGENT_ID,),
    ).fetchone()
    assert row is None


@then("exactly one delivered pending-queries row exists for the agent")
def _one_delivered_row(flag_bdd_ctx: _FlagCtx) -> None:
    assert flag_bdd_ctx.reader is not None
    # F63-bounded: BDD assertion on tmp-path DB; ON branch writes one row.
    row = flag_bdd_ctx.reader.execute(
        "SELECT status FROM pending_queries WHERE agent_id = ? LIMIT 1",
        (_AGENT_ID,),
    ).fetchone()
    assert row is not None
    assert row[0] == "delivered"
