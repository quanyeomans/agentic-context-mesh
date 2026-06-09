"""Step impls for feature_flag_entity_summary_indexing_enabled.feature.

F54 both-branch coverage — the steps mirror the tiny worker-tick
gating helper from the integration test (Slice A) so the BDD scenario
exercises the same flag-read path the real worker will use in Slice B.

F1/F2-clean: ``FakeFeatureFlagResolver`` is the canonical fake; no
monkey-patching.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from pytest_bdd import given, parsers, then, when

from tests.fakes import FakeEntitySummaryProjector, FakeFeatureFlagResolver

pytestmark = pytest.mark.bdd


@dataclass
class _Ctx:
    flag_on: bool = False
    projector: FakeEntitySummaryProjector | None = None


@pytest.fixture
def projector_ctx() -> _Ctx:
    return _Ctx(projector=FakeEntitySummaryProjector())


@given(parsers.parse("the operator has the entity-summary-indexing flag set to {state}"))
def _flag_state(projector_ctx: _Ctx, state: str) -> None:
    projector_ctx.flag_on = state.strip().lower() == "true"


@when("the worker tick stage evaluates whether to run the projector")
def _evaluate_tick(projector_ctx: _Ctx) -> None:
    assert projector_ctx.projector is not None
    resolver = (
        FakeFeatureFlagResolver().with_flag("entity_summary_indexing_enabled", True)
        if projector_ctx.flag_on
        else FakeFeatureFlagResolver().with_flag("entity_summary_indexing_enabled", False)
    )
    if resolver.get("entity_summary_indexing_enabled"):
        projector_ctx.projector.tick(per_tick_max_items=200)


@then("the projector is not ticked")
def _not_ticked(projector_ctx: _Ctx) -> None:
    assert projector_ctx.projector is not None
    assert projector_ctx.projector.ticks == []


@then("the projector is ticked once")
def _ticked_once(projector_ctx: _Ctx) -> None:
    assert projector_ctx.projector is not None
    assert projector_ctx.projector.ticks == [200]
