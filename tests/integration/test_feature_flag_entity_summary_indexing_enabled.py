"""F54 both-branch coverage for ``entity_summary_indexing_enabled``
(ADR-036, Slice B — #460).

Drives the real :func:`run_entity_summary_projector_tick` dispatcher
with the flag pinned ON / OFF via the public
:class:`EntitySummaryProjectorDeps` seam:

* **OFF (default)** — dispatcher returns ``None``; no projector built,
  no Neo4j touched, no chunks written. ADR-036 §Cutover default-safe
  contract.
* **ON** — dispatcher builds a projector via ``projector_factory`` and
  ticks it; the result envelope surfaces back to the caller.

F1/F2-clean. Both literal ``with_flag("entity_summary_indexing_enabled",
False)`` and ``..., True)`` strings appear below for the F54 detector.
"""

from __future__ import annotations

import pytest

from kairix.core.protocols import EntitySummaryProjectionResult
from kairix.knowledge.entities.summary_projector import (
    EntitySummaryProjectorDeps,
    run_entity_summary_projector_tick,
)
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


def test_off_branch_projector_factory_never_called() -> None:
    """Flag OFF → :func:`run_entity_summary_projector_tick` returns ``None``,
    the projector_factory is never invoked, no Neo4j or chunk-writer
    touched.

    Sabotage-proof: drop the ``if not deps.flag_reader(): return None``
    guard in the dispatcher and the factory ends up called even on
    the OFF branch — projector.ticks gains a [200] entry.
    """
    resolver = _resolver(flag_on=False)
    projector = FakeEntitySummaryProjector(
        result=EntitySummaryProjectionResult(projected=5),
    )
    deps = EntitySummaryProjectorDeps(
        flag_reader=lambda: resolver.get("entity_summary_indexing_enabled"),
        projector_factory=lambda: projector,  # type: ignore[arg-type] — fake satisfies Protocol shape
    )
    out = run_entity_summary_projector_tick(deps)
    assert out is None
    assert projector.ticks == []


def test_on_branch_projector_built_and_ticked_once() -> None:
    """Flag ON → factory builds the projector, dispatcher ticks it
    once with ``per_tick_max_items`` forwarded; result envelope
    surfaces verbatim."""
    resolver = _resolver(flag_on=True)
    projector = FakeEntitySummaryProjector(
        result=EntitySummaryProjectionResult(projected=3, updated=1, skipped=4, failed=0),
    )
    deps = EntitySummaryProjectorDeps(
        flag_reader=lambda: resolver.get("entity_summary_indexing_enabled"),
        projector_factory=lambda: projector,  # type: ignore[arg-type] — fake satisfies Protocol shape
        per_tick_max_items=150,
    )
    out = run_entity_summary_projector_tick(deps)
    assert out is not None
    assert (out.projected, out.updated, out.skipped, out.failed) == (3, 1, 4, 0)
    assert projector.ticks == [150]


def test_on_branch_repeat_dispatcher_calls_tick_each_time() -> None:
    """Each dispatcher invocation calls the projector once when the
    flag stays ON — locks per-tick flag-reading contract (so flipping
    the flag mid-loop takes effect on the next tick, not just at
    startup)."""
    resolver = _resolver(flag_on=True)
    projector = FakeEntitySummaryProjector()
    deps = EntitySummaryProjectorDeps(
        flag_reader=lambda: resolver.get("entity_summary_indexing_enabled"),
        projector_factory=lambda: projector,  # type: ignore[arg-type] — fake satisfies Protocol shape
    )
    run_entity_summary_projector_tick(deps)
    run_entity_summary_projector_tick(deps)
    run_entity_summary_projector_tick(deps)
    assert projector.ticks == [200, 200, 200]


def test_on_branch_default_factory_yields_idle_tick_when_no_neo4j_wired() -> None:
    """Flag ON + default deps (no override) → dispatcher builds the
    Slice B placeholder projector whose poll path hits the
    ``RuntimeError`` ("no Neo4j wired") and returns an idle
    :class:`EntitySummaryProjectionResult`. Locks the safe-misconfig
    contract: an operator who flips the flag before Slice C ships the
    live factory doesn't crash the worker loop."""
    resolver = _resolver(flag_on=True)
    deps = EntitySummaryProjectorDeps(
        flag_reader=lambda: resolver.get("entity_summary_indexing_enabled"),
    )
    out = run_entity_summary_projector_tick(deps)
    assert out is not None
    assert (out.projected, out.updated, out.skipped, out.failed) == (0, 0, 0, 0)
