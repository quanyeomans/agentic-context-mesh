"""Step definitions for feature_flag_intent_confidence_gated_boosts.feature.

F54 both-branch coverage — drives
:func:`kairix.core.search.boosts.intent_confidence_passes` directly with
the ``flag_reader`` DI seam pinned ON / OFF, then asserts that the
boost-gate decision matches the flag state + confidence.

F1/F2-clean: no monkey-patching, no env-var manipulation. The
``flag_reader`` kwarg is the public DI seam by design.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.core.search.boosts import intent_confidence_passes
from kairix.core.search.intent import QueryIntent

pytestmark = pytest.mark.bdd


_MIN_CONFIDENCE = 0.5
_HIGH_CONFIDENCE = 0.9
_LOW_CONFIDENCE = 0.2


@dataclass
class _Ctx:
    flag_on: bool = False
    fired: bool | None = None


@pytest.fixture
def gated_boost_ctx() -> _Ctx:
    return _Ctx()


@given(parsers.parse("the operator has the intent-confidence-gated-boosts flag set to {state}"))
def _flag_state(gated_boost_ctx: _Ctx, state: str) -> None:
    gated_boost_ctx.flag_on = state.strip().lower() == "true"


@when("the boost gate is asked about a low-confidence intent match")
def _ask_gate_low(gated_boost_ctx: _Ctx) -> None:
    flag_reader = (lambda: True) if gated_boost_ctx.flag_on else (lambda: False)
    gated_boost_ctx.fired = intent_confidence_passes(
        {"intent": QueryIntent.TEMPORAL, "intent_confidence": _LOW_CONFIDENCE},
        QueryIntent.TEMPORAL,
        _MIN_CONFIDENCE,
        flag_reader=flag_reader,
    )


@when("the boost gate is asked about a high-confidence intent match")
def _ask_gate_high(gated_boost_ctx: _Ctx) -> None:
    flag_reader = (lambda: True) if gated_boost_ctx.flag_on else (lambda: False)
    gated_boost_ctx.fired = intent_confidence_passes(
        {"intent": QueryIntent.TEMPORAL, "intent_confidence": _HIGH_CONFIDENCE},
        QueryIntent.TEMPORAL,
        _MIN_CONFIDENCE,
        flag_reader=flag_reader,
    )


@then("the boost fires")
def _fires(gated_boost_ctx: _Ctx) -> None:
    assert gated_boost_ctx.fired is True


@then("the boost is skipped")
def _skipped(gated_boost_ctx: _Ctx) -> None:
    assert gated_boost_ctx.fired is False
