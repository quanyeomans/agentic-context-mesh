"""Unit tests for ``EntityFirstRoutingBoost`` (#429 Phase 2b).

Entity-first routing lifts the ``entity-summaries`` collection (the
ADR-036 projector's ``entity://<QID>`` chunks, tier ``reference`` x0.6)
to the top of the candidate set for ``QueryIntent.ENTITY`` queries
("tell me about X" / "who is X"). It is a ranker swap, so it sits behind
the default-off ``entity_first_routing_enabled`` feature flag — read live
via the ``flag_reader`` DI seam so tests drive both branches without
monkey-patching (F1/F2-clean).

Default-safe contract: flag OFF ⇒ ``boost()`` is a structural no-op and
pre-#429 ranking is preserved byte-for-byte.
"""

from __future__ import annotations

import pytest

from kairix.core.search.boosts import EntityFirstRoutingBoost
from kairix.core.search.config import EntityFirstRoutingConfig
from kairix.core.search.intent import QueryIntent
from kairix.core.search.rrf import FusedResult

pytestmark = pytest.mark.unit


_FLAG_ON = lambda: True  # noqa: E731 — terse zero-arg flag_reader DI seam for tests
_FLAG_OFF = lambda: False  # noqa: E731


def _entity_row(score: float = 0.5) -> FusedResult:
    """An entity-summary candidate — collection + ``entity://`` source URI."""
    return FusedResult(
        path="entity://Q42",
        collection="entity-summaries",
        title="Douglas Adams",
        snippet="English author and humourist.",
        rrf_score=score,
        boosted_score=score,
    )


def _vault_row(score: float = 0.5) -> FusedResult:
    """A plain vault candidate — must never be touched by this boost."""
    return FusedResult(
        path="notes/about.md",
        collection="vault",
        title="About",
        snippet="A note.",
        rrf_score=score,
        boosted_score=score,
    )


def _entity_context() -> dict:
    return {"intent": QueryIntent.ENTITY, "intent_confidence": 1.0}


def test_flag_on_entity_intent_multiplies_entity_summary_score() -> None:
    """Flag ON + ENTITY intent → entity-summary row xfactor; vault row untouched."""
    boost = EntityFirstRoutingBoost(
        config=EntityFirstRoutingConfig(factor=3.0),
        flag_reader=_FLAG_ON,
    )
    entity, vault = _entity_row(0.5), _vault_row(0.5)

    boost.boost([entity, vault], "tell me about Douglas Adams", _entity_context())

    assert entity.boosted_score == pytest.approx(1.5)  # 0.5 x 3.0
    assert vault.boosted_score == pytest.approx(0.5)  # untouched


def test_flag_off_is_a_noop_even_for_entity_intent() -> None:
    """Default-safe: flag OFF leaves every score byte-for-byte unchanged."""
    boost = EntityFirstRoutingBoost(
        config=EntityFirstRoutingConfig(factor=3.0),
        flag_reader=_FLAG_OFF,
    )
    entity, vault = _entity_row(0.5), _vault_row(0.5)

    boost.boost([entity, vault], "tell me about Douglas Adams", _entity_context())

    assert entity.boosted_score == pytest.approx(0.5)
    assert vault.boosted_score == pytest.approx(0.5)


def test_non_entity_intent_is_a_noop_even_with_flag_on() -> None:
    """Intent gate: a KEYWORD query never routes entity-first."""
    boost = EntityFirstRoutingBoost(
        config=EntityFirstRoutingConfig(factor=3.0),
        flag_reader=_FLAG_ON,
    )
    entity = _entity_row(0.5)

    boost.boost([entity], "douglas adams", {"intent": QueryIntent.KEYWORD, "intent_confidence": 1.0})

    assert entity.boosted_score == pytest.approx(0.5)


def test_matches_by_entity_uri_prefix_when_collection_label_absent() -> None:
    """Robustness: an ``entity://`` row with a blank collection still boosts.

    The CLI badge + MCP envelope key off the ``entity://`` prefix; the
    boost honours the same well-known marker so a row whose collection
    label was dropped upstream is still routed first.
    """
    boost = EntityFirstRoutingBoost(
        config=EntityFirstRoutingConfig(factor=2.0),
        flag_reader=_FLAG_ON,
    )
    row = FusedResult(
        path="entity://Q937",
        collection="",
        title="Albert Einstein",
        snippet="Theoretical physicist.",
        rrf_score=0.4,
        boosted_score=0.4,
    )

    boost.boost([row], "who is Einstein", _entity_context())

    assert row.boosted_score == pytest.approx(0.8)  # 0.4 x 2.0


def test_matches_by_collection_when_uri_prefix_absent() -> None:
    """A row in the entity-summaries collection whose path is NOT an
    ``entity://`` URI is still routed — the collection label is the
    fallback marker."""
    boost = EntityFirstRoutingBoost(
        config=EntityFirstRoutingConfig(factor=2.0),
        flag_reader=_FLAG_ON,
    )
    row = FusedResult(
        path="entity-summaries/Q5.md",
        collection="entity-summaries",
        title="Marie Curie",
        snippet="Physicist and chemist.",
        rrf_score=0.4,
        boosted_score=0.4,
    )

    boost.boost([row], "tell me about Marie Curie", _entity_context())

    assert row.boosted_score == pytest.approx(0.8)  # 0.4 x 2.0


def test_non_entity_row_is_left_untouched() -> None:
    """A plain row (neither collection nor URI matches) keeps its score."""
    boost = EntityFirstRoutingBoost(
        config=EntityFirstRoutingConfig(factor=5.0),
        flag_reader=_FLAG_ON,
    )
    row = _vault_row(0.5)

    boost.boost([row], "tell me about something", _entity_context())

    assert row.boosted_score == pytest.approx(0.5)


def test_routed_entity_sorts_to_front() -> None:
    """The boost re-sorts by boosted_score so a lower-base-score entity
    summary leads a higher-base-score plain note once routed."""
    boost = EntityFirstRoutingBoost(
        config=EntityFirstRoutingConfig(factor=5.0),
        flag_reader=_FLAG_ON,
    )
    entity = _entity_row(0.3)  # lower base score
    vault = _vault_row(0.5)  # higher base score — leads before the boost

    out = boost.boost([vault, entity], "tell me about Douglas Adams", _entity_context())

    # entity 0.3 x 5 = 1.5 > vault 0.5 → entity leads after the re-sort.
    assert out[0].path.startswith("entity://")
    assert out[1].path == "notes/about.md"


def test_per_row_failure_is_isolated_and_never_raises() -> None:
    """A malformed row (no path/collection/score) is skipped, not fatal."""
    boost = EntityFirstRoutingBoost(flag_reader=_FLAG_ON)

    class _Broken:
        path = "entity://Q1"
        collection = "entity-summaries"

        @property
        def boosted_score(self) -> float:
            raise RuntimeError("boom")

    healthy = _entity_row(0.5)
    out = boost.boost([_Broken(), healthy], "tell me about X", _entity_context())

    assert out is not None
    assert healthy.boosted_score == pytest.approx(1.5)  # default factor 3.0
