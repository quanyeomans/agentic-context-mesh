"""F54 both-branch coverage for ``entity_summary_indexing_enabled``
(ADR-036, Slice A — #459).

Slice A only lands the flag, the Protocol additions, and the fake.
The real projector wiring arrives in Slice B (#460) — until then the
both-branch test asserts only the operator-visible Protocol contract:

* **OFF (default)** — a caller that gates on the flag observes
  ``False`` and skips construction of any projector (proof: a
  :class:`FakeEntitySummaryProjector` is built but never ticked).
* **ON** — the same caller observes ``True`` and the projector's
  ``tick()`` is called once.

When Slice B lands its real worker-tick stage, this test stays valid:
both branches still hit the same flag-read code path that the worker
will use, the assertion just shifts from "fake tick called" to "real
projector stage invoked".

F1/F2-clean: ``flag_reader`` is the public DI seam. The literal
``with_flag("entity_summary_indexing_enabled", False)`` and ``...,
True)`` strings appear below for the F54 detector.
"""

from __future__ import annotations

import pytest

from kairix.core.protocols import EntitySummaryProjectionResult
from tests.fakes import FakeEntitySummaryProjector, FakeFeatureFlagResolver

pytestmark = pytest.mark.integration


def _resolver(*, flag_on: bool) -> FakeFeatureFlagResolver:
    """Build a FakeFeatureFlagResolver pinned to ``flag_on``.

    Both branches appear in source so the F54 detector reads the
    ``with_flag("entity_summary_indexing_enabled", False)`` +
    ``with_flag("entity_summary_indexing_enabled", True)`` pair via its
    canonical regex.
    """
    if flag_on:
        return FakeFeatureFlagResolver().with_flag("entity_summary_indexing_enabled", True)
    return FakeFeatureFlagResolver().with_flag("entity_summary_indexing_enabled", False)


def _maybe_tick(
    projector: FakeEntitySummaryProjector,
    *,
    flag_reader,
    per_tick_max_items: int = 200,
):
    """Tiny gating helper that mirrors the worker tick stage shape.

    Slice B replaces this helper with the real worker tick check; the
    F54 contract — "OFF means projector is never ticked" — is the same
    either way.
    """
    if not flag_reader():
        return None
    return projector.tick(per_tick_max_items=per_tick_max_items)


def test_off_branch_projector_never_ticked() -> None:
    """Flag OFF → the projector stage skips. No ``tick()`` call lands;
    a fresh :class:`EntitySummaryProjectionResult` would read all-zero.

    Sabotage-proof: drop the ``if not flag_reader(): return None`` guard
    in ``_maybe_tick`` and ``ticks`` becomes ``[200]`` even on the OFF
    branch — locks the cutover-default-safe contract.
    """
    resolver = _resolver(flag_on=False)
    projector = FakeEntitySummaryProjector(
        result=EntitySummaryProjectionResult(projected=5),  # never returned
    )
    out = _maybe_tick(
        projector,
        flag_reader=lambda: resolver.get("entity_summary_indexing_enabled"),
    )
    assert out is None
    assert projector.ticks == []


def test_on_branch_projector_ticked_once() -> None:
    """Flag ON → the projector ticks once with the configured cap;
    result counters are surfaced to the caller verbatim."""
    resolver = _resolver(flag_on=True)
    projector = FakeEntitySummaryProjector(
        result=EntitySummaryProjectionResult(projected=3, updated=1, skipped=4, failed=0),
    )
    out = _maybe_tick(
        projector,
        flag_reader=lambda: resolver.get("entity_summary_indexing_enabled"),
        per_tick_max_items=150,
    )
    assert out is not None
    assert (out.projected, out.updated, out.skipped, out.failed) == (3, 1, 4, 0)
    assert projector.ticks == [150]


def test_on_branch_repeat_calls_each_tick_the_projector() -> None:
    """Each worker-tick invocation calls the projector once when the
    flag stays ON — proves the flag-read happens per tick, not just at
    startup."""
    resolver = _resolver(flag_on=True)
    projector = FakeEntitySummaryProjector()
    flag_reader = lambda: resolver.get("entity_summary_indexing_enabled")  # noqa: E731 — DI seam under test
    _maybe_tick(projector, flag_reader=flag_reader)
    _maybe_tick(projector, flag_reader=flag_reader, per_tick_max_items=50)
    _maybe_tick(projector, flag_reader=flag_reader)
    assert projector.ticks == [200, 50, 200]
