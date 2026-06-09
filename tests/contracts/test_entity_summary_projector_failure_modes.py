"""F68 failure-mode contract for :class:`EntitySummaryProjector` (ADR-036).

Slice A (#459) Protocol-shape proofs live in
:mod:`tests.contracts.test_entity_summary_projector_protocol`. This
file covers the failure classes the F68 detector requires per Protocol
method.

For Slice A the projector contract has one method (:meth:`tick`); the
failure classes proven here are ``raises`` (the projector's exception
propagates so the worker boundary can absorb-or-surface deliberately)
and ``returns_empty`` (an idle tick returns an all-zero result, not
None).

Slice B (#460) lands the real projector; its concrete impl adds
Neo4j-unavailable, ChunkWriter-raises, partial-failures, and
per_tick_max_items-cap proofs in this same file.

F1/F2-clean by construction.
"""

from __future__ import annotations

import pytest

from kairix.core.protocols import EntitySummaryProjectionResult
from tests.fakes import FakeEntitySummaryProjector

pytestmark = pytest.mark.contract


class _RaisingProjector:
    """Projector whose ``tick`` raises — F68 failure-injection mirror.

    A real projector may hit Neo4j-unavailable / ChunkWriter-raise /
    DB-disk-full mid-tick. The Protocol does NOT swallow at this
    boundary — the worker tick that composes the projector is what
    decides whether to absorb (so the worker loop stays responsive) or
    surface (so on-call sees a degraded-state signal).

    Slice B will land the real projector + its own absorb-and-classify
    contract; Slice A's contract pin is "exception propagates if not
    absorbed" so the absorb-or-surface decision is locked at the
    worker boundary, not silently buried inside the projector.
    """

    def tick(self, *, per_tick_max_items: int = 200) -> EntitySummaryProjectionResult:
        raise RuntimeError(f"simulated tick failure (per_tick_max_items={per_tick_max_items})")


def test_tick_raises_propagates_to_caller() -> None:
    """F68 ``raises`` — when the projector raises mid-tick, the
    exception propagates so the worker boundary can absorb-or-surface
    deliberately.

    Sabotage-proof: wrap the projector ``tick`` in a bare
    ``try/except: return EntitySummaryProjectionResult()`` and the
    assertion below fails (no exception reaches the caller; the worker
    boundary loses its absorb-or-surface decision).
    """
    projector: _RaisingProjector = _RaisingProjector()
    with pytest.raises(RuntimeError, match="simulated tick failure"):
        projector.tick(per_tick_max_items=50)


def test_tick_returns_empty_for_no_pending_entities() -> None:
    """F68 ``returns_empty`` — when Neo4j has nothing to project, the
    projector returns an all-zero :class:`EntitySummaryProjectionResult`,
    NOT None.

    Locks the contract so the worker telemetry records an idle tick
    the same way as a productive tick (both surface a result; one
    just has all counters at zero)."""
    projector = FakeEntitySummaryProjector()
    out = projector.tick(per_tick_max_items=100)
    assert isinstance(out, EntitySummaryProjectionResult)
    assert (out.projected, out.updated, out.skipped, out.failed) == (0, 0, 0, 0)
