"""F54 both-branch coverage for the ``intent_confidence_gated_boosts`` feature flag.

Exercises :func:`kairix.core.search.boosts.intent_confidence_passes`
with the flag pinned ON and OFF via the ``flag_reader`` DI seam. Asserts:

* **OFF (default)** — a matching intent fires the boost even when the
  classifier's confidence is below the boost's ``min_intent_confidence``.
  Preserves pre-#456 ranking byte-for-byte.
* **ON** — a matching intent with low confidence skips the boost; only
  matches above ``min_intent_confidence`` fire. Ambiguous-query
  protection.

F1/F2-clean: ``flag_reader`` is the public DI seam — no monkey-patching,
no env-var manipulation. F54 detector recognises the literal
``with_flag("intent_confidence_gated_boosts", False)`` and ``...,
True)`` strings below; they appear in source even though only one
fires per test.
"""

from __future__ import annotations

import pytest

from kairix.core.search.boosts import intent_confidence_passes
from kairix.core.search.intent import QueryIntent
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.integration


_MIN_CONFIDENCE = 0.5


def _resolver(*, flag_on: bool) -> FakeFeatureFlagResolver:
    """Build a FakeFeatureFlagResolver pinned to ``flag_on``.

    F54 detector reads both branches literally from this file via the
    ``with_flag("intent_confidence_gated_boosts", False)`` +
    ``with_flag("intent_confidence_gated_boosts", True)`` heuristic, so
    both branches must appear in source even though only one fires per
    test invocation.
    """
    if flag_on:
        return FakeFeatureFlagResolver().with_flag("intent_confidence_gated_boosts", True)
    return FakeFeatureFlagResolver().with_flag("intent_confidence_gated_boosts", False)


def test_off_branch_low_confidence_still_fires_boost() -> None:
    """OFF — boost gate ignores confidence and fires whenever intent matches.

    Locks the pre-#456 ranking contract: operators who haven't flipped
    the flag see byte-for-byte identical boost behaviour even when the
    pipeline now emits intent_confidence in the context dict.
    """
    resolver = _resolver(flag_on=False)
    fired = intent_confidence_passes(
        {"intent": QueryIntent.TEMPORAL, "intent_confidence": 0.1},
        QueryIntent.TEMPORAL,
        _MIN_CONFIDENCE,
        flag_reader=lambda: resolver.get("intent_confidence_gated_boosts"),
    )
    assert fired is True


def test_on_branch_low_confidence_skips_boost() -> None:
    """ON — boost gate enforces confidence floor; low-confidence skips.

    The core #456 contract: ambiguous queries (low margin between
    primary intent and runner-up) fall back to plain RRF instead of
    triggering a potentially-wrong boost.
    """
    resolver = _resolver(flag_on=True)
    fired = intent_confidence_passes(
        {"intent": QueryIntent.TEMPORAL, "intent_confidence": 0.1},
        QueryIntent.TEMPORAL,
        _MIN_CONFIDENCE,
        flag_reader=lambda: resolver.get("intent_confidence_gated_boosts"),
    )
    assert fired is False


def test_on_branch_high_confidence_still_fires_boost() -> None:
    """ON — boost still fires when confidence is comfortably above the floor.

    Locks that confidence-gating is a filter on *ambiguous* matches —
    high-margin classifications still get the boost they always did.
    """
    resolver = _resolver(flag_on=True)
    fired = intent_confidence_passes(
        {"intent": QueryIntent.TEMPORAL, "intent_confidence": 0.9},
        QueryIntent.TEMPORAL,
        _MIN_CONFIDENCE,
        flag_reader=lambda: resolver.get("intent_confidence_gated_boosts"),
    )
    assert fired is True


def test_intent_mismatch_skips_regardless_of_flag_or_confidence() -> None:
    """Intent mismatch is the legacy gate; it short-circuits before
    flag / confidence are even consulted. Locks the precedence order
    so a future refactor doesn't accidentally invert it."""
    resolver = _resolver(flag_on=False)
    fired = intent_confidence_passes(
        {"intent": QueryIntent.PROCEDURAL, "intent_confidence": 0.99},
        QueryIntent.TEMPORAL,  # expected != context.intent
        _MIN_CONFIDENCE,
        flag_reader=lambda: resolver.get("intent_confidence_gated_boosts"),
    )
    assert fired is False
