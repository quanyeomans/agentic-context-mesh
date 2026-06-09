"""Contract tests for ADR-036 — :class:`EntitySummaryProjector` Protocol.

Exercises the Protocol's structural contract via :class:`FakeEntitySummaryProjector`:

* the fake is :func:`isinstance`-compatible with the Protocol
* :meth:`tick` honours its bounded-per-tick contract — calls with
  ``per_tick_max_items`` are recorded so a caller can prove the cap
  reached the projector
* the result carries the four counters declared in ADR-036 §Protocol
  (``projected``, ``updated``, ``skipped``, ``failed``)

Slice A only proves the Protocol shape against the fake — the real
projector implementation arrives in Slice B (#460) and brings its own
F68 failure-injection contract tests over the Cypher polling, the
delete-then-write path, and the Neo4j mark-indexed write.

F1/F2-clean by construction — every test composes the fake through its
normal kwargs.
"""

from __future__ import annotations

import pytest

from kairix.core.protocols import EntitySummaryProjectionResult, EntitySummaryProjector
from tests.fakes import FakeEntitySummaryProjector

pytestmark = pytest.mark.contract


def test_fake_satisfies_entity_summary_projector_protocol() -> None:
    """The fake passes ``isinstance(fake, EntitySummaryProjector)``.

    Locks the Protocol surface — if a future commit drops or renames
    :meth:`tick` on the Protocol, this assertion fails.
    """
    projector = FakeEntitySummaryProjector()
    assert isinstance(projector, EntitySummaryProjector)


def test_projector_tick_returns_protocol_result_shape() -> None:
    """:meth:`tick` returns an :class:`EntitySummaryProjectionResult` with all
    four counters present.

    Sabotage-proof: drop one of the four fields from the dataclass and
    the attribute access here raises ``AttributeError``.
    """
    result = EntitySummaryProjectionResult(projected=3, updated=1, skipped=2, failed=0)
    projector = FakeEntitySummaryProjector(result=result)
    out = projector.tick(per_tick_max_items=50)
    assert out.projected == 3
    assert out.updated == 1
    assert out.skipped == 2
    assert out.failed == 0


def test_projector_tick_records_per_tick_max_items() -> None:
    """The fake records every ``per_tick_max_items`` it was called with.

    Proves the bounded-per-tick contract is plumbed through the
    Protocol kwarg, not silently ignored by callers.
    """
    projector = FakeEntitySummaryProjector()
    projector.tick(per_tick_max_items=100)
    projector.tick(per_tick_max_items=50)
    assert projector.ticks == [100, 50]


def test_projector_result_defaults_are_zero() -> None:
    """A fresh :class:`EntitySummaryProjectionResult` reads zero on every
    counter — locks the default-safe contract so callers that miss a
    branch (e.g. flag OFF) get a meaningful empty result, not None."""
    result = EntitySummaryProjectionResult()
    assert (result.projected, result.updated, result.skipped, result.failed) == (0, 0, 0, 0)


def test_projector_result_is_frozen() -> None:
    """:class:`EntitySummaryProjectionResult` is frozen — operators
    can't accidentally mutate counters after the projector returns.

    Sabotage-proof: drop ``frozen=True`` and the assignment below
    succeeds instead of raising ``FrozenInstanceError``.
    """
    import dataclasses

    result = EntitySummaryProjectionResult(projected=1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.projected = 99  # type: ignore[misc] — proving FrozenInstanceError; the type-checker reasonably objects so we silence and assert at runtime instead
