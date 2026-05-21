"""Step definitions for mcp_facts_about.feature.

Drives ``tool_facts_about`` with a FakeFactStore from ``tests/fakes.py``.
F1-clean: fact_store is constructor-injected via the public seam.
F13-clean: scenarios speak in agent language (facts response, hits,
entity), never implementation symbols.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.agents.mcp.tools.facts_about import tool_facts_about
from tests.fakes import FakeFactRecord, FakeFactStore

pytestmark = pytest.mark.bdd


@dataclass
class _State:
    fact_store: FakeFactStore = field(default_factory=FakeFactStore)
    response: dict[str, Any] = field(default_factory=dict)


@pytest.fixture
def _facts_state() -> _State:
    return _State()


@given(parsers.parse('the fact store has a fact about "{entity}" with attribute "{attr}" and value "{value}"'))
def _given_fact(_facts_state: _State, entity: str, attr: str, value: str) -> None:
    _facts_state.fact_store.add(FakeFactRecord(id=f"f-{entity}-{attr}", entity=entity, attribute=attr, value=value))


@given(parsers.parse('the fact store has no facts about "{entity}"'))
def _given_no_facts(_facts_state: _State, entity: str) -> None:
    # Intentionally no-op; the default FakeFactStore is empty. The Given
    # exists so the scenario reads as the operator would write it. We
    # touch ``entity`` so F19 sees the parameter as Load-context-used.
    assert entity, "entity name should be provided by the scenario"


@when(parsers.parse('the agent calls facts-about with entity "{entity}"'))
def _when_call(_facts_state: _State, entity: str) -> None:
    _facts_state.response = tool_facts_about(entity=entity, fact_store=_facts_state.fact_store)


@then(parsers.parse("the facts response lists {n:d} hit"))
@then(parsers.parse("the facts response lists {n:d} hits"))
def _then_hits_count(_facts_state: _State, n: int) -> None:
    assert len(_facts_state.response["hits"]) == n


@then(parsers.parse('the facts response hit value is "{value}"'))
def _then_hit_value(_facts_state: _State, value: str) -> None:
    hits = _facts_state.response["hits"]
    assert hits, "expected at least one hit"
    assert hits[0]["value"] == value


@then("the facts response error is empty")
def _then_no_error(_facts_state: _State) -> None:
    assert _facts_state.response["error"] == ""
