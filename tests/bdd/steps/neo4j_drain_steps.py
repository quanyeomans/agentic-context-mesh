"""Step implementations for neo4j_drain.feature (GH #334).

Steps drive the production :func:`kairix.core.curator.drain.run_neo4j_drain_tick`
through the F47 sanctioned factory entry point
:func:`kairix.core.factory.build_neo4j_drainer`. The graph backend is
provided by :class:`tests.fakes.FakeDrainGraphRepository` (no real
Neo4j connection). Per F46 every When step has a call-graph depth ≤ 2
to either the drainer's ``tick`` method or the free function.

F1-clean: no @patch / module-attribute substitution on kairix.
F2-clean: no ``KAIRIX_*`` env-var manipulation.
F46-clean: composition through ``kairix.core.factory.build_neo4j_drainer``.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.core import factory
from kairix.core.curator.drain import NeoDrainResult
from kairix.core.db.schema import create_schema
from tests.fakes import FakeDrainGraphRepository

pytestmark = pytest.mark.bdd


@dataclass
class _Ctx:
    db: sqlite3.Connection
    repo: FakeDrainGraphRepository
    staged_ids: list[int] = field(default_factory=list)
    staged_names: dict[str, int] = field(default_factory=dict)
    result: NeoDrainResult | None = None


@pytest.fixture
def drain_ctx(tmp_path: Path) -> _Ctx:
    db_path = tmp_path / "neo4j_drain.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    return _Ctx(db=db, repo=FakeDrainGraphRepository(available=True))


def _insert_signal(
    db: sqlite3.Connection,
    *,
    kind: str = "person",
    value: str,
    modified_at: str,
    pushed_to_neo4j: int = 0,
    push_attempt_count: int = 0,
) -> int:
    cur = db.execute(
        "INSERT INTO entity_signals (kind, value, source_uri, modified_at, confidence, "
        "sensitivity, pushed_to_neo4j, push_attempt_count) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (kind, value, f"vault://{value}.md", modified_at, 0.9, "internal", pushed_to_neo4j, push_attempt_count),
    )
    db.commit()
    assert cur.lastrowid is not None
    return int(cur.lastrowid)


# ---------------------------------------------------------------------------
# Givens
# ---------------------------------------------------------------------------


@given("the operator has staged three person signals into entity_signals")
def given_three_staged_person_signals(drain_ctx: _Ctx) -> None:
    for i in range(3):
        sid = _insert_signal(
            drain_ctx.db,
            value=f"person-{i}",
            modified_at=f"2026-05-2{i}T10:00:00Z",
        )
        drain_ctx.staged_ids.append(sid)
        drain_ctx.staged_names[f"person-{i}"] = sid


@given("the graph backend is reachable")
def given_graph_reachable(drain_ctx: _Ctx) -> None:
    drain_ctx.repo.set_available(True)


@given("the graph backend is unreachable")
def given_graph_unreachable(drain_ctx: _Ctx) -> None:
    drain_ctx.repo.set_available(False)


@given("the operator has staged three person signals named alpha, bravo, charlie")
def given_three_named_signals(drain_ctx: _Ctx) -> None:
    for i, name in enumerate(["alpha", "bravo", "charlie"]):
        sid = _insert_signal(
            drain_ctx.db,
            value=name,
            modified_at=f"2026-05-2{i}T10:00:00Z",
        )
        drain_ctx.staged_names[name] = sid


@given(parsers.parse("the graph backend raises on the value {value}"))
def given_repo_raises_on_value(drain_ctx: _Ctx, value: str) -> None:
    drain_ctx.repo.raise_on_value = value
    drain_ctx.repo.set_available(True)


@given("the operator has staged one person signal with push_attempt_count already 3")
def given_stalled_signal(drain_ctx: _Ctx) -> None:
    sid = _insert_signal(
        drain_ctx.db,
        value="stalled-person",
        modified_at="2026-05-20T10:00:00Z",
        pushed_to_neo4j=-1,
        push_attempt_count=3,
    )
    drain_ctx.staged_names["stalled-person"] = sid


@given("the operator has staged five person signals with mixed modified_at timestamps")
def given_five_mixed_signals(drain_ctx: _Ctx) -> None:
    # Insert in non-chronological order so the SELECT must do the sorting.
    rows = [
        ("newest", "2026-05-25T10:00:00Z"),
        ("oldest", "2026-05-21T10:00:00Z"),
        ("middle", "2026-05-23T10:00:00Z"),
        ("second-oldest", "2026-05-22T10:00:00Z"),
        ("second-newest", "2026-05-24T10:00:00Z"),
    ]
    for name, modified_at in rows:
        sid = _insert_signal(drain_ctx.db, value=name, modified_at=modified_at)
        drain_ctx.staged_names[name] = sid


# ---------------------------------------------------------------------------
# Whens
# ---------------------------------------------------------------------------


@when(parsers.parse("the operator runs one drain tick with batch_size {batch_size:d}"))
def when_run_drain_tick(drain_ctx: _Ctx, batch_size: int) -> None:
    drainer = factory.build_neo4j_drainer(db=drain_ctx.db, repo=drain_ctx.repo, batch_size=batch_size)
    drain_ctx.result = drainer.tick()


# ---------------------------------------------------------------------------
# Thens
# ---------------------------------------------------------------------------


def _read_flag(db: sqlite3.Connection, signal_id: int) -> int:
    row = db.execute(
        "SELECT pushed_to_neo4j FROM entity_signals WHERE id = ?",
        (signal_id,),
    ).fetchone()
    assert row is not None, f"signal id={signal_id} not found"
    return int(row[0])


def _read_last_error(db: sqlite3.Connection, signal_id: int) -> str | None:
    row = db.execute(
        "SELECT last_push_error FROM entity_signals WHERE id = ?",
        (signal_id,),
    ).fetchone()
    assert row is not None, f"signal id={signal_id} not found"
    return None if row[0] is None else str(row[0])


@then(parsers.parse("the result reports pushed equal to {expected:d}"))
def then_pushed_equals(drain_ctx: _Ctx, expected: int) -> None:
    assert drain_ctx.result is not None, "drain tick was not run"
    assert drain_ctx.result.pushed == expected, (
        f"expected pushed={expected}, got {drain_ctx.result.pushed} (full: {drain_ctx.result})"
    )


@then("the result reports neo4j_available is false")
def then_neo4j_unavailable(drain_ctx: _Ctx) -> None:
    assert drain_ctx.result is not None
    assert drain_ctx.result.neo4j_available is False, (
        f"expected neo4j_available=False, got {drain_ctx.result.neo4j_available}"
    )


@then("every staged signal has pushed_to_neo4j flipped to 1")
def then_every_signal_pushed(drain_ctx: _Ctx) -> None:
    for sid in drain_ctx.staged_ids:
        assert _read_flag(drain_ctx.db, sid) == 1, f"signal id={sid} did not flip to 1"


@then("no staged signal has pushed_to_neo4j flipped")
def then_no_signal_flipped(drain_ctx: _Ctx) -> None:
    for sid in drain_ctx.staged_ids:
        assert _read_flag(drain_ctx.db, sid) == 0, f"signal id={sid} flipped despite outage"


@then(parsers.parse("the graph backend received three MERGE Person calls"))
def then_three_merge_calls(drain_ctx: _Ctx) -> None:
    person_calls = [c for c in drain_ctx.repo.cypher_calls if "MERGE (p:Person" in c[0]]
    assert len(person_calls) == 3, (
        f"expected 3 Person MERGE calls, got {len(person_calls)} (all calls: {len(drain_ctx.repo.cypher_calls)})"
    )


@then(parsers.parse("the signal named {name} has pushed_to_neo4j equal to {expected:d}"))
def then_named_signal_flag(drain_ctx: _Ctx, name: str, expected: int) -> None:
    sid = drain_ctx.staged_names[name]
    flag = _read_flag(drain_ctx.db, sid)
    assert flag == expected, f"signal {name!r} (id={sid}): expected pushed_to_neo4j={expected}, got {flag}"


@then(parsers.parse("the signal named {name} carries a non-empty last_push_error"))
def then_named_signal_has_error(drain_ctx: _Ctx, name: str) -> None:
    sid = drain_ctx.staged_names[name]
    err = _read_last_error(drain_ctx.db, sid)
    assert err is not None and len(err) > 0, f"signal {name!r} (id={sid}) has empty last_push_error: {err!r}"


@then(parsers.parse("the stalled signal still has pushed_to_neo4j equal to {expected:d}"))
def then_stalled_still_at(drain_ctx: _Ctx, expected: int) -> None:
    sid = drain_ctx.staged_names["stalled-person"]
    flag = _read_flag(drain_ctx.db, sid)
    assert flag == expected, f"stalled signal id={sid}: expected pushed_to_neo4j={expected}, got {flag}"


@then("the two oldest staged signals have pushed_to_neo4j equal to 1")
def then_two_oldest_pushed(drain_ctx: _Ctx) -> None:
    oldest_two = ["oldest", "second-oldest"]
    for name in oldest_two:
        sid = drain_ctx.staged_names[name]
        flag = _read_flag(drain_ctx.db, sid)
        assert flag == 1, f"oldest signal {name!r} (id={sid}) did not flip: got pushed_to_neo4j={flag}"


@then("the three newest staged signals still have pushed_to_neo4j equal to 0")
def then_three_newest_unpushed(drain_ctx: _Ctx) -> None:
    newest_three = ["middle", "second-newest", "newest"]
    for name in newest_three:
        sid = drain_ctx.staged_names[name]
        flag = _read_flag(drain_ctx.db, sid)
        assert flag == 0, f"newest signal {name!r} (id={sid}) was pushed prematurely: got pushed_to_neo4j={flag}"
