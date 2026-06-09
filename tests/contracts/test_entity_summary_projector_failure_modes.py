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


# ---------------------------------------------------------------------------
# Slice B (#460) — real projector failure injection
# ---------------------------------------------------------------------------


_FIXED_TICK = "2026-06-09T00:00:00Z"


def _row(*, name: str, qid: str, summary: str, prior_hash: str = "") -> dict:
    return {
        "name": name,
        "qid": qid,
        "summary": summary,
        "prior_hash": prior_hash,
        "summary_source": "wikidata",
    }


def test_tick_returns_empty_when_neo4j_unavailable() -> None:
    """F68 ``unavailable`` — Neo4j cypher raises on the poll → projector
    returns an all-zero result, never propagates. Locks the worker
    boundary's absorb-at-poll contract.

    Sabotage-proof: drop the ``try/except`` around the cypher call in
    ``_fetch_pending`` and the assertion below fires
    ``RuntimeError`` instead of catching the empty result.
    """
    from kairix.knowledge.entities.summary_projector import EntitySummaryProjectorImpl
    from tests.fakes import FakeChunkWriter, FakeGraphRepository

    neo4j = FakeGraphRepository(raises=RuntimeError("neo4j-unavailable"))
    projector = EntitySummaryProjectorImpl(
        neo4j=neo4j,
        chunk_writer=FakeChunkWriter(),
        clock=lambda: _FIXED_TICK,
    )
    out = projector.tick(per_tick_max_items=10)
    assert (out.projected, out.updated, out.skipped, out.failed) == (0, 0, 0, 0)


def test_tick_returns_partial_when_chunk_writer_raises_on_some_entities() -> None:
    """F68 ``returns_partial`` — per-entity ChunkWriter failure is
    counted in ``failed``; the remaining entities still project.

    Locks ADR-036 §Expected behaviours #6 failure-isolation contract.
    """
    from kairix.knowledge.entities.summary_projector import EntitySummaryProjectorImpl
    from tests.fakes import FakeGraphRepository

    class _FlakyWriter:
        def __init__(self) -> None:
            self.upsert_calls = 0

        def upsert(self, chunks):
            self.upsert_calls += 1
            if self.upsert_calls == 1:
                raise RuntimeError("flake on first write")
            return len(list(chunks))

        def delete_by_source_uri(self, _uri: str) -> int:
            return 0

    neo4j = FakeGraphRepository(
        cypher_rows=[
            _row(name="Ada", qid="Q1", summary="first"),
            _row(name="Bob", qid="Q2", summary="second"),
        ],
    )
    projector = EntitySummaryProjectorImpl(
        neo4j=neo4j,
        chunk_writer=_FlakyWriter(),
        clock=lambda: _FIXED_TICK,
    )
    out = projector.tick(per_tick_max_items=10)
    assert out.projected == 1
    assert out.failed == 1


def test_tick_returns_partial_respects_per_tick_max_items_cap() -> None:
    """F68 ``returns_partial`` (cap variant) — per_tick_max_items is
    forwarded into the Cypher LIMIT param.

    Sabotage-proof: hard-code ``per_tick_max_items`` to 200 in
    ``_fetch_pending`` and the assertion below catches the wrong
    LIMIT bind value reaching Cypher."""
    from kairix.knowledge.entities.summary_projector import EntitySummaryProjectorImpl
    from tests.fakes import FakeChunkWriter, FakeGraphRepository

    neo4j = FakeGraphRepository(cypher_rows=[])
    projector = EntitySummaryProjectorImpl(
        neo4j=neo4j,
        chunk_writer=FakeChunkWriter(),
        clock=lambda: _FIXED_TICK,
    )
    projector.tick(per_tick_max_items=42)
    assert neo4j.cypher_calls
    _query, params = neo4j.cypher_calls[0]
    assert params == {"per_tick_max_items": 42}
