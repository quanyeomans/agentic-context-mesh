"""Contract tests for ResultEnvelope (ADR v2 §7 per-source freshness shape).

Pins:
* Freshness state transitions through fresh / stale / access_revoked /
  not_yet_synced based on age + container access state.
* Envelope wraps results without replacing the list shape.
* ``as_search_output()`` returns the bare results tuple for back-compat.
* ``total_results`` is derived from the results count.

Sabotage-prove targets:
- Freshness classification: change ``if age_seconds <=
  fresh_threshold_seconds`` to ``if age_seconds <= 0`` → confirm
  test_fresh_state_under_threshold fails → restore.
"""

from __future__ import annotations

import pytest

from kairix.core.connectors.result_envelope import (
    ResultChunk,
    ResultEnvelope,
    SourceFreshness,
    build_envelope,
    build_freshness,
)
from kairix.core.connectors.scope_profile_resolver import ExcludedCollection

pytestmark = pytest.mark.contract


def _chunk(*, source_uri: str, score: float = 0.5) -> ResultChunk:
    return ResultChunk(
        chunk_id=f"chunk-{source_uri}",
        text=f"text for {source_uri}",
        score=score,
        collection="some-collection",
        sensitivity="internal",
        source_uri=source_uri,
    )


def test_fresh_state_under_threshold() -> None:
    f = build_freshness(source="obsidian", last_synced_at="2026-05-23T00:00:00Z", age_seconds=600)
    assert f.state == "fresh"


def test_stale_state_above_fresh_threshold() -> None:
    f = build_freshness(source="obsidian", last_synced_at="2026-05-22T00:00:00Z", age_seconds=7200)
    assert f.state == "stale"


def test_not_yet_synced_when_no_age() -> None:
    f = build_freshness(source="obsidian", last_synced_at=None, age_seconds=None)
    assert f.state == "not_yet_synced"


def test_negative_age_treated_as_not_yet_synced() -> None:
    f = build_freshness(source="obsidian", last_synced_at="2026-05-24T00:00:00Z", age_seconds=-30)
    assert f.state == "not_yet_synced"


def test_access_revoked_overrides_age() -> None:
    """When container access is REVOKED, state is access_revoked regardless of age."""
    f = build_freshness(
        source="sharepoint",
        last_synced_at="2026-05-22T00:00:00Z",
        age_seconds=60,
        container_access_state="REVOKED",
    )
    assert f.state == "access_revoked"


def test_envelope_total_results_derived_from_results_count() -> None:
    chunks = (_chunk(source_uri="a"), _chunk(source_uri="b"), _chunk(source_uri="c"))
    envelope = build_envelope(
        results=chunks,
        included_collections=("alpha",),
    )
    assert envelope.total_results == 3
    assert envelope.results == chunks


def test_envelope_as_search_output_returns_bare_results() -> None:
    """Back-compat: callers that don't yet consume the envelope keep working."""
    chunks = (_chunk(source_uri="x"),)
    envelope = build_envelope(results=chunks, included_collections=("alpha",))
    assert envelope.as_search_output() == chunks


def test_envelope_carries_excluded_collections_with_reason() -> None:
    excluded = (
        ExcludedCollection(
            name="restricted-corpus",
            reason="actor_lacks_read",
            escalation_hint="grant can_read",
        ),
    )
    envelope = build_envelope(
        results=(),
        included_collections=(),
        excluded_collections=excluded,
    )
    assert envelope.excluded_collections == excluded
    assert envelope.excluded_collections[0].reason == "actor_lacks_read"


def test_envelope_carries_freshness_tuple() -> None:
    freshness = (
        build_freshness(source="obsidian", last_synced_at="2026-05-23T00:00:00Z", age_seconds=60),
        build_freshness(source="sharepoint", last_synced_at=None, age_seconds=None),
    )
    envelope = build_envelope(
        results=(),
        included_collections=(),
        freshness=freshness,
    )
    assert envelope.freshness == freshness
    assert envelope.freshness[0].state == "fresh"
    assert envelope.freshness[1].state == "not_yet_synced"


def test_envelope_dataclass_is_frozen() -> None:
    envelope = build_envelope(results=(), included_collections=())
    with pytest.raises((AttributeError, TypeError)):
        envelope.total_results = 99  # type: ignore[misc]  # F3-rationale: intentional mutation attempt to prove frozen.


def test_result_chunk_dataclass_is_frozen() -> None:
    chunk = _chunk(source_uri="x")
    with pytest.raises((AttributeError, TypeError)):
        chunk.score = 0.99  # type: ignore[misc]  # F3-rationale: intentional mutation attempt to prove frozen.


def test_envelope_isinstance_of_result_envelope() -> None:
    envelope = build_envelope(results=(), included_collections=())
    assert isinstance(envelope, ResultEnvelope)


def test_source_freshness_dataclass_is_frozen() -> None:
    f = build_freshness(source="x", last_synced_at=None, age_seconds=None)
    assert isinstance(f, SourceFreshness)
    with pytest.raises((AttributeError, TypeError)):
        f.source = "mutated"  # type: ignore[misc]  # F3-rationale: intentional mutation attempt to prove frozen.
