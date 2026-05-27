"""Step implementations for connector_cursor_persistence.feature (F62 reference).

The scenarios drive a real :class:`kairix.core.connectors.pipeline.ConnectorPipeline`
against scripted :class:`tests.fakes.FakeSourceConnector` instances
that simulate the SharePoint deltaLink shape (opaque token) and the
quiet-tick path. Per F46 the steps reach the pipeline through its
documented DI seams (constructor injection of stores + fakes) — no
direct ``*Pipeline(...)`` magic, no monkeypatch, no @patch.

F1-clean: stores are constructed via their public constructors and
injected via the pipeline's documented kwargs.
F2-clean: no ``KAIRIX_*`` env-var manipulation.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from pytest_bdd import given, then, when

from kairix.core import factory
from kairix.core.connectors.pipeline import ConnectorPipeline
from kairix.core.db.schema import create_schema
from kairix.core.protocols import ChangeEvent
from tests.fakes import FakeChunkWriter, FakeEntityGraphSink, FakeExtractor, FakeSourceConnector

_DELTALINK_TICK1 = '{"drive-1": "https://graph.microsoft.com/v1.0/drives/drive-1/root/delta?token=tick1"}'
_DELTALINK_TICK2 = '{"drive-1": "https://graph.microsoft.com/v1.0/drives/drive-1/root/delta?token=tick2"}'


@dataclass
class _ScenarioState:
    db: sqlite3.Connection
    pipeline: ConnectorPipeline
    source_name: str
    events: list[ChangeEvent] = field(default_factory=list)
    delta_link_t1: str = _DELTALINK_TICK1
    delta_link_t2: str = _DELTALINK_TICK2
    list_changes_calls: list[object] = field(default_factory=list)


@pytest.fixture
def cursor_state(tmp_path: Path) -> _ScenarioState:
    db_path = tmp_path / "cursor_persistence.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    pipeline = factory.build_connector_pipeline(
        db=db,
        collection="cursor-test-collection",
        chunk_writer=FakeChunkWriter(),
        entity_graph_sink=FakeEntityGraphSink(),
    )
    return _ScenarioState(db=db, pipeline=pipeline, source_name="")


@given('a connector "graph-style-source" whose next_cursor() returns an opaque deltaLink')
def given_opaque_deltalink_connector(cursor_state: _ScenarioState) -> None:
    cursor_state.source_name = "graph-style-source"


@given("tick 1 emits two change events with later modified_at timestamps than the deltaLink")
def given_two_events(cursor_state: _ScenarioState) -> None:
    cursor_state.events = [
        ChangeEvent(op="modified", item_id=f"doc-{i}.md", modified_at=f"2026-05-26T11:0{i}:00Z") for i in (1, 2)
    ]


@given('a connector "graph-style-source" with a prior cursor persisted from a previous tick')
def given_prior_cursor_persisted(cursor_state: _ScenarioState) -> None:
    cursor_state.source_name = "graph-style-source"
    seed_events = [ChangeEvent(op="modified", item_id="seed.md", modified_at="2026-05-26T10:00:00Z")]
    body = ("body. " * 30).encode("utf-8")
    seed_conn = FakeSourceConnector(
        name=cursor_state.source_name,
        events=seed_events,
        content={"seed.md": body},
        cursor_token=cursor_state.delta_link_t1,
    )
    cursor_state.pipeline.run_batch(seed_conn, FakeExtractor())
    persisted = cursor_state.db.execute(
        "SELECT cursor_token FROM connector_cursors WHERE source_name = ?",
        (cursor_state.source_name,),
    ).fetchone()
    assert persisted is not None and persisted[0] == cursor_state.delta_link_t1


@given("the next tick emits zero events and the connector's next_cursor() returns None")
def given_quiet_tick_no_advance(cursor_state: _ScenarioState) -> None:
    cursor_state.events = []
    cursor_state.delta_link_t2 = ""  # marker — connector will use cursor_token=None


@given("the next tick emits zero events but the connector's next_cursor() advances")
def given_quiet_tick_with_advance(cursor_state: _ScenarioState) -> None:
    cursor_state.events = []
    # delta_link_t2 retains its module-level default value (advances).


@when('the operator runs two consecutive pipeline ticks for "graph-style-source"')
def when_two_ticks(cursor_state: _ScenarioState) -> None:
    body = ("body. " * 30).encode("utf-8")
    connector = FakeSourceConnector(
        name=cursor_state.source_name,
        events=cursor_state.events,
        content={ev.item_id: body for ev in cursor_state.events},
        cursor_token=cursor_state.delta_link_t1,
    )
    cursor_state.pipeline.run_batch(connector, FakeExtractor())  # tick 1
    cursor_state.pipeline.run_batch(connector, FakeExtractor())  # tick 2
    cursor_state.list_changes_calls = list(connector.list_changes_calls)


@when('the operator runs the next pipeline tick for "graph-style-source"')
def when_next_tick(cursor_state: _ScenarioState) -> None:
    cursor_token = cursor_state.delta_link_t2 if cursor_state.delta_link_t2 else None
    connector = FakeSourceConnector(
        name=cursor_state.source_name,
        events=cursor_state.events,
        cursor_token=cursor_token,
    )
    cursor_state.pipeline.run_batch(connector, FakeExtractor())


@then("tick 1 persists the deltaLink to connector_cursors, not any event modified_at")
def then_deltalink_persisted(cursor_state: _ScenarioState) -> None:
    stored = cursor_state.db.execute(
        "SELECT cursor_token FROM connector_cursors WHERE source_name = ?",
        (cursor_state.source_name,),
    ).fetchone()
    assert stored is not None
    assert stored[0] == cursor_state.delta_link_t1
    for ev in cursor_state.events:
        assert ev.modified_at not in str(stored[0])


@then("tick 2 calls list_changes with the deltaLink, not None")
def then_tick2_uses_stored_cursor(cursor_state: _ScenarioState) -> None:
    assert len(cursor_state.list_changes_calls) == 2
    assert cursor_state.list_changes_calls[0] is None
    assert cursor_state.list_changes_calls[1] == cursor_state.delta_link_t1


@then("the persisted cursor still equals the prior cursor")
def then_cursor_preserved(cursor_state: _ScenarioState) -> None:
    stored = cursor_state.db.execute(
        "SELECT cursor_token FROM connector_cursors WHERE source_name = ?",
        (cursor_state.source_name,),
    ).fetchone()
    assert stored is not None
    assert stored[0] == cursor_state.delta_link_t1


@then("the orchestrator did not clobber the cursor row with None")
def then_cursor_not_clobbered(cursor_state: _ScenarioState) -> None:
    stored = cursor_state.db.execute(
        "SELECT cursor_token FROM connector_cursors WHERE source_name = ?",
        (cursor_state.source_name,),
    ).fetchone()
    assert stored is not None
    assert stored[0] is not None


@then("the persisted cursor equals the advanced token from this tick")
def then_cursor_advanced(cursor_state: _ScenarioState) -> None:
    stored = cursor_state.db.execute(
        "SELECT cursor_token FROM connector_cursors WHERE source_name = ?",
        (cursor_state.source_name,),
    ).fetchone()
    assert stored is not None
    assert stored[0] == cursor_state.delta_link_t2
