"""Step definitions for feature_flag_entity_first_routing_enabled.feature.

F54 both-branch coverage — drives
:class:`kairix.core.search.boosts.EntityFirstRoutingBoost` directly with
the ``flag_reader`` DI seam pinned ON / OFF, then asserts the entity
summary is (or is not) lifted above a plain note.

F1/F2-clean: no monkey-patching, no env-var manipulation. The
``flag_reader`` kwarg is the public DI seam by design.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.core.search.boosts import EntityFirstRoutingBoost
from kairix.core.search.config import EntityFirstRoutingConfig
from kairix.core.search.intent import QueryIntent
from kairix.core.search.rrf import FusedResult

pytestmark = pytest.mark.bdd


_BASE_SCORE = 0.5
_FACTOR = 3.0


@dataclass
class _Ctx:
    flag_on: bool = False
    entity: FusedResult | None = None
    note: FusedResult | None = None


@pytest.fixture
def routing_ctx() -> _Ctx:
    return _Ctx()


def _apply(ctx: _Ctx, intent: QueryIntent) -> None:
    """Build a tied (entity-summary, plain-note) pair and run the boost."""
    ctx.entity = FusedResult(
        path="entity://Q42",
        collection="entity-summaries",
        title="Douglas Adams",
        snippet="English author and humourist.",
        rrf_score=_BASE_SCORE,
        boosted_score=_BASE_SCORE,
    )
    ctx.note = FusedResult(
        path="notes/about.md",
        collection="vault",
        title="About",
        snippet="A plain vault note.",
        rrf_score=_BASE_SCORE,
        boosted_score=_BASE_SCORE,
    )
    boost = EntityFirstRoutingBoost(
        config=EntityFirstRoutingConfig(factor=_FACTOR),
        flag_reader=lambda: ctx.flag_on,
    )
    boost.boost(
        [ctx.entity, ctx.note],
        "tell me about Douglas Adams",
        {"intent": intent, "intent_confidence": 1.0},
    )


@given(parsers.parse("the operator has the entity-first-routing flag set to {state}"))
def _set_flag(routing_ctx: _Ctx, state: str) -> None:
    routing_ctx.flag_on = state.strip().lower() == "true"


@when("an entity-intent search ranks an entity summary against a plain note")
def _entity_search(routing_ctx: _Ctx) -> None:
    _apply(routing_ctx, QueryIntent.ENTITY)


@when("a keyword search ranks an entity summary against a plain note")
def _keyword_search(routing_ctx: _Ctx) -> None:
    _apply(routing_ctx, QueryIntent.KEYWORD)


@then("the entity summary is lifted above the plain note")
def _lifted(routing_ctx: _Ctx) -> None:
    assert routing_ctx.entity is not None and routing_ctx.note is not None
    assert routing_ctx.entity.boosted_score > routing_ctx.note.boosted_score


@then("the entity summary keeps its original score")
def _unchanged(routing_ctx: _Ctx) -> None:
    assert routing_ctx.entity is not None
    assert routing_ctx.entity.boosted_score == pytest.approx(routing_ctx.entity.rrf_score)
