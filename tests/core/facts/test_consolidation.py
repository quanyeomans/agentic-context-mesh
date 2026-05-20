"""Unit tests for :class:`kairix.core.facts.ConsolidationPass`.

Each test drives behaviour through the public ``kairix.core.facts``
surface only (F5: no internal imports) and uses fakes from
``tests/fakes.py`` (F1: no monkeypatching) plus tiny in-test
contradict callables to pin per-pair behaviour.

Every test below was sabotage-proven during authoring: a concrete
mutation in production was identified, the test was confirmed to fail
under that mutation, and production was restored. The proof transcript
is in the commit body.
"""

from __future__ import annotations

import logging

import pytest

from kairix.core.facts import (
    ConsolidationOutcome,
    ConsolidationPass,
    default_contradict,
)
from tests.fakes import FakeFactRecord, FakeFactStore

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Constructor / DI surface
# ---------------------------------------------------------------------------


def test_constructor_accepts_fact_store_and_contradict_kwargs() -> None:
    """Sabotage-proof: rename either kwarg in ``__init__`` and this test
    raises TypeError instead of constructing the pass cleanly."""
    pass_obj = ConsolidationPass(
        fact_store=FakeFactStore(),
        contradict=default_contradict,
    )
    assert isinstance(pass_obj, ConsolidationPass)


def test_custom_contradict_callable_replaces_default() -> None:
    """DI works: a scripted callable replaces the default behaviour.

    Sabotage-proof: hard-code ``self._contradict = default_contradict``
    in ``__init__`` and this test fails because the scripted callable
    is ignored — the verdict reverts to value-based comparison.
    """
    store = FakeFactStore()
    prior = FakeFactRecord(id="p1", entity="X", attribute="status", value="single")
    new = FakeFactRecord(id="n1", entity="X", attribute="status", value="single")
    store.add(prior)
    store.add(new)

    # Custom callable says "contradiction" even though values match —
    # default would say "same".
    def always_contradict(_prior: object, _new: object) -> str:
        return "contradiction"

    pass_obj = ConsolidationPass(fact_store=store, contradict=always_contradict)
    outcome = pass_obj.process(new)

    assert outcome.superseded_ids == ("p1",)
    assert outcome.coexists_with == ()


# ---------------------------------------------------------------------------
# Outcome semantics — no priors / same / update / multiple
# ---------------------------------------------------------------------------


def test_no_existing_facts_returns_empty_superseded() -> None:
    """A new fact in an empty store has nothing to supersede.

    Sabotage-proof: return ``("dummy",)`` instead of ``tuple(superseded)``
    in :class:`ConsolidationOutcome` construction and this test sees a
    non-empty tuple.
    """
    store = FakeFactStore()
    new = FakeFactRecord(id="n1", entity="X", attribute="status", value="single")
    store.add(new)

    pass_obj = ConsolidationPass(fact_store=store, contradict=default_contradict)
    outcome = pass_obj.process(new)

    assert outcome.new_fact_id == "n1"
    assert outcome.superseded_ids == ()
    assert outcome.coexists_with == ()


def test_existing_same_value_coexists_no_supersession() -> None:
    """Prior fact with the same value → ``"same"`` verdict → coexists.

    Sabotage-proof: change the ``"same"`` branch of ``process`` to also
    call ``supersede`` and the coexist tuple shrinks while superseded
    grows.
    """
    store = FakeFactStore()
    prior = FakeFactRecord(id="p1", entity="X", attribute="status", value="single")
    store.add(prior)
    new = FakeFactRecord(id="n1", entity="X", attribute="status", value="single")
    store.add(new)

    pass_obj = ConsolidationPass(fact_store=store, contradict=default_contradict)
    outcome = pass_obj.process(new)

    assert outcome.superseded_ids == ()
    assert outcome.coexists_with == ("p1",)
    # Prior remains live (not superseded).
    live = store.find_conflicts(entity="X", attribute="status")
    live_ids = {f.id for f in live}
    assert "p1" in live_ids


def test_existing_different_value_marked_superseded() -> None:
    """Prior fact with a different value → ``"update"`` verdict → supersede.

    Sabotage-proof: skip the ``fact_store.supersede`` call in the
    ``update``/``contradiction`` branch — the prior fact stays live and
    this assertion (it's been removed from the live result) fails.
    """
    store = FakeFactStore()
    prior = FakeFactRecord(id="p1", entity="X", attribute="status", value="single")
    store.add(prior)
    new = FakeFactRecord(id="n1", entity="X", attribute="status", value="married")
    store.add(new)

    pass_obj = ConsolidationPass(fact_store=store, contradict=default_contradict)
    outcome = pass_obj.process(new)

    assert outcome.superseded_ids == ("p1",)
    assert outcome.coexists_with == ()
    # Prior no longer surfaces in live find_conflicts.
    live = store.find_conflicts(entity="X", attribute="status")
    live_ids = {f.id for f in live}
    assert "p1" not in live_ids
    assert "n1" in live_ids


def test_multiple_existing_facts_each_processed_independently() -> None:
    """Two priors with different values each get superseded; aggregate carries both.

    Sabotage-proof: break out of the per-prior loop after the first
    supersession and only one id appears in ``superseded_ids``.
    """
    store = FakeFactStore()
    prior1 = FakeFactRecord(id="p1", entity="X", attribute="status", value="single")
    prior2 = FakeFactRecord(id="p2", entity="X", attribute="status", value="engaged")
    store.add(prior1)
    store.add(prior2)
    new = FakeFactRecord(id="n1", entity="X", attribute="status", value="married")
    store.add(new)

    pass_obj = ConsolidationPass(fact_store=store, contradict=default_contradict)
    outcome = pass_obj.process(new)

    assert set(outcome.superseded_ids) == {"p1", "p2"}
    assert outcome.coexists_with == ()


def test_mixed_same_and_update_split_into_two_tuples() -> None:
    """One prior matches (coexists) and another differs (superseded).

    Sabotage-proof: append both verdicts to ``superseded`` (drop the
    else branch) and ``coexists_with`` becomes empty.
    """
    store = FakeFactStore()
    prior_same = FakeFactRecord(id="p_same", entity="X", attribute="status", value="single")
    prior_diff = FakeFactRecord(id="p_diff", entity="X", attribute="status", value="engaged")
    store.add(prior_same)
    store.add(prior_diff)
    # ``new.value == "single"`` matches prior_same, contradicts prior_diff.
    new = FakeFactRecord(id="n1", entity="X", attribute="status", value="single")
    store.add(new)

    pass_obj = ConsolidationPass(fact_store=store, contradict=default_contradict)
    outcome = pass_obj.process(new)

    assert outcome.superseded_ids == ("p_diff",)
    assert outcome.coexists_with == ("p_same",)


# ---------------------------------------------------------------------------
# Namespace isolation
# ---------------------------------------------------------------------------


def test_namespace_isolation_no_cross_engagement_consolidation() -> None:
    """Facts in different namespaces are never considered conflicts.

    Sabotage-proof: drop the ``namespace=`` kwarg from the
    ``find_conflicts`` call in ``process`` and the cross-namespace
    prior shows up as a supersession candidate.
    """
    store = FakeFactStore()
    prior_other = FakeFactRecord(
        id="p_other",
        entity="X",
        attribute="status",
        value="married",
        namespace="eng-other",
    )
    store.add(prior_other)
    new = FakeFactRecord(
        id="n1",
        entity="X",
        attribute="status",
        value="single",
        namespace="eng-self",
    )
    store.add(new)

    pass_obj = ConsolidationPass(fact_store=store, contradict=default_contradict)
    outcome = pass_obj.process(new)

    # Cross-namespace prior is invisible to this consolidation pass.
    assert outcome.superseded_ids == ()
    assert outcome.coexists_with == ()
    # And it remains live in its own namespace.
    other_live = store.find_conflicts(entity="X", attribute="status", namespace="eng-other")
    assert {f.id for f in other_live} == {"p_other"}


# ---------------------------------------------------------------------------
# Surface C — structured log on supersession
# ---------------------------------------------------------------------------


def test_supersession_emits_structured_log_line(caplog: pytest.LogCaptureFixture) -> None:
    """Every supersession emits a structured INFO log line.

    Sabotage-proof: remove the ``logger.info(...)`` call in
    ``ConsolidationPass.process`` and the caplog filter returns no
    matching records.
    """
    store = FakeFactStore()
    prior = FakeFactRecord(id="p1", entity="Acme", attribute="status", value="active")
    store.add(prior)
    new = FakeFactRecord(id="n1", entity="Acme", attribute="status", value="dormant")
    store.add(new)

    pass_obj = ConsolidationPass(fact_store=store, contradict=default_contradict)
    with caplog.at_level(logging.INFO, logger="kairix.core.facts.consolidation"):
        pass_obj.process(new)

    matching = [r for r in caplog.records if "consolidation.supersede" in r.getMessage()]
    assert len(matching) == 1
    msg = matching[0].getMessage()
    assert "entity=Acme" in msg
    assert "attribute=status" in msg
    assert "old=p1" in msg
    assert "new=n1" in msg
    assert "verdict=update" in msg


def test_no_log_emitted_when_no_supersession(caplog: pytest.LogCaptureFixture) -> None:
    """``"same"`` verdicts do not emit consolidation.supersede log lines.

    Sabotage-proof: move the ``logger.info`` call outside the conditional
    and the caplog filter sees one record despite no actual supersession.
    """
    store = FakeFactStore()
    prior = FakeFactRecord(id="p1", entity="X", attribute="status", value="single")
    store.add(prior)
    new = FakeFactRecord(id="n1", entity="X", attribute="status", value="single")
    store.add(new)

    pass_obj = ConsolidationPass(fact_store=store, contradict=default_contradict)
    with caplog.at_level(logging.INFO, logger="kairix.core.facts.consolidation"):
        pass_obj.process(new)

    matching = [r for r in caplog.records if "consolidation.supersede" in r.getMessage()]
    assert matching == []


# ---------------------------------------------------------------------------
# Default contradict callable behaviour
# ---------------------------------------------------------------------------


def test_default_contradict_returns_same_for_equal_values() -> None:
    """Sabotage-proof: invert the equality check (``!=``) and this returns
    ``"update"`` for matching values."""
    a = FakeFactRecord(id="a", entity="X", attribute="y", value="v")
    b = FakeFactRecord(id="b", entity="X", attribute="y", value="v")
    assert default_contradict(a, b) == "same"


def test_default_contradict_returns_update_for_different_values() -> None:
    """Sabotage-proof: return ``"same"`` unconditionally and the second
    branch of this assertion fails."""
    a = FakeFactRecord(id="a", entity="X", attribute="y", value="v1")
    b = FakeFactRecord(id="b", entity="X", attribute="y", value="v2")
    assert default_contradict(a, b) == "update"


# ---------------------------------------------------------------------------
# Self-skip — the new fact is excluded from its own consolidation pass
# ---------------------------------------------------------------------------


def test_self_in_find_conflicts_is_skipped() -> None:
    """If the store returns the new fact in ``find_conflicts`` (because
    the caller persisted before consolidating), the pass skips it
    rather than trying to supersede itself.

    Sabotage-proof: remove the ``if prior.id == new_fact.id: continue``
    guard and the new fact appears as a coexist (or worse, supersedes
    itself depending on the callable).
    """
    store = FakeFactStore()
    new = FakeFactRecord(id="n1", entity="X", attribute="status", value="single")
    store.add(new)  # caller persisted *before* consolidation, mirroring ingest_chat

    pass_obj = ConsolidationPass(fact_store=store, contradict=default_contradict)
    outcome = pass_obj.process(new)

    assert outcome.superseded_ids == ()
    assert outcome.coexists_with == ()
    assert outcome.new_fact_id == "n1"


# ---------------------------------------------------------------------------
# Outcome shape — value-object guarantees
# ---------------------------------------------------------------------------


def test_outcome_is_frozen_dataclass() -> None:
    """Sabotage-proof: drop ``frozen=True`` from the dataclass decorator
    and this mutation succeeds silently instead of raising."""
    outcome = ConsolidationOutcome(
        new_fact_id="n1",
        superseded_ids=("p1",),
        coexists_with=("p2",),
    )
    with pytest.raises((AttributeError, Exception)):  # FrozenInstanceError
        outcome.new_fact_id = "different"  # type: ignore[misc]  # frozen dataclass assignment is the exact behaviour under test
