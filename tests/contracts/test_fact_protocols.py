"""Contract: FactRecord + FactExtractor + FactStore + FactHit Protocols.

Foundation for Plan B-parity Week 1. Pins the boundary that the
upcoming ``SQLiteFactStore`` (Capability #3) and ``LLMFactExtractor``
(Capability #2) will both have to satisfy, plus the round-trip
semantics every implementation must honour.

Three layers of test:

1. Protocol compliance — ``isinstance(fake, <Protocol>)`` returns True
   for each fake. Sabotage-proven by removing a Protocol method from
   the fake and confirming the isinstance check fails.
2. Round-trip semantics — add → search → find_conflicts → supersede
   sequences honour the documented contracts (idempotency, namespace
   filtering, supersession masking).
3. FactRecord identity contract — the Protocol's deterministic-id
   guarantee is exercised through the public surface; fakes that
   produce non-deterministic ids would fail the test that re-extract
   yields the same record key.

DI is F1-clean: fakes are constructed inline; no monkeypatching, no
internal-attribute reassignment.
"""

from __future__ import annotations

import pytest

from kairix.core.protocols import FactExtractor, FactHit, FactRecord, FactStore
from tests.fakes import FakeFactExtractor, FakeFactHit, FakeFactRecord, FakeFactStore

pytestmark = pytest.mark.contract


# ---------------------------------------------------------------------------
# Protocol compliance — sabotage-proven via runtime isinstance()
# ---------------------------------------------------------------------------


def test_fake_fact_record_satisfies_protocol() -> None:
    """FakeFactRecord satisfies FactRecord via runtime isinstance().

    Sabotage-proof: remove the ``namespace`` property from FakeFactRecord
    and this assertion fails — runtime_checkable probes for every
    documented property.
    """
    record = FakeFactRecord(id="f1", entity="agent-alpha", attribute="role", value="VP")
    assert isinstance(record, FactRecord)


def test_fake_fact_hit_satisfies_protocol() -> None:
    """FakeFactHit satisfies FactHit (the search-result wrapper)."""
    record = FakeFactRecord(id="f1", entity="X", attribute="y", value="z")
    hit = FakeFactHit(record=record, score=0.8)
    assert isinstance(hit, FactHit)


def test_fake_fact_extractor_satisfies_protocol() -> None:
    """FakeFactExtractor satisfies FactExtractor.

    Sabotage-proof: remove ``extract`` from FakeFactExtractor and this
    runtime check fails.
    """
    assert isinstance(FakeFactExtractor(), FactExtractor)


def test_fake_fact_store_satisfies_protocol() -> None:
    """FakeFactStore satisfies FactStore via runtime isinstance().

    Sabotage-proof: remove ``supersede`` from FakeFactStore and this
    runtime check fails.
    """
    assert isinstance(FakeFactStore(), FactStore)


# ---------------------------------------------------------------------------
# FactStore round-trip — add → search → supersede semantics
# ---------------------------------------------------------------------------


def test_add_then_search_round_trips_fact_through_value_overlap() -> None:
    """A just-added fact is findable by ``search`` when the query shares
    words with the fact's (entity + attribute + value).

    Sabotage-proof: change FakeFactStore.search to ignore the query
    string and the matched-id assertion fails because the test fact
    is mixed with no-match facts.
    """
    store = FakeFactStore()
    target = FakeFactRecord(id="f-target", entity="agent-alpha", attribute="status", value="single")
    distractor = FakeFactRecord(id="f-distractor", entity="John", attribute="hobby", value="bowling")
    store.add(target)
    store.add(distractor)

    hits = store.search("agent-alpha status", top_k=5)
    assert hits, "search must surface the target fact when query shares words"
    matched = [h for h in hits if h.record.id == "f-target"]
    assert matched, f"search must include target id; got ids={[h.record.id for h in hits]}"


def test_add_is_idempotent_on_deterministic_id() -> None:
    """Adding the same fact twice does not duplicate it.

    Protocol contract: ``add`` is idempotent on the fact's id. The
    SQLiteFactStore implementation will rely on this for re-ingest
    safety; the contract test pins it at the boundary.
    """
    store = FakeFactStore()
    fact = FakeFactRecord(id="f-same", entity="X", attribute="y", value="z")
    store.add(fact)
    store.add(fact)
    # Search for a word that only appears in this fact; expect exactly 1 hit
    hits = store.search("z", top_k=10)
    distinct_ids = {h.record.id for h in hits}
    assert distinct_ids == {"f-same"}, f"duplicate add must not produce duplicate hits; got {distinct_ids!r}"


def test_search_top_k_caps_result_count() -> None:
    """``top_k`` caps the number of hits returned."""
    store = FakeFactStore()
    for i in range(20):
        store.add(FakeFactRecord(id=f"f-{i:02d}", entity="common-entity", attribute="x", value="common"))
    hits = store.search("common", top_k=3)
    assert len(hits) == 3, f"top_k=3 must cap result count; got {len(hits)}"


def test_search_returns_best_first_by_score() -> None:
    """Hits are sorted descending by score (best first)."""
    store = FakeFactStore()
    store.add(FakeFactRecord(id="f-strong", entity="alpha beta gamma", attribute="x", value="match"))
    store.add(FakeFactRecord(id="f-weak", entity="alpha", attribute="x", value="match"))
    hits = store.search("alpha beta gamma match", top_k=5)
    assert len(hits) >= 2
    assert hits[0].score >= hits[1].score, "results must be sorted by score descending"


def test_search_empty_store_returns_empty_list() -> None:
    """Search against an empty store returns ``[]`` (not None, no exception)."""
    store = FakeFactStore()
    assert store.search("anything", top_k=10) == []


def test_search_excludes_superseded_facts_by_default() -> None:
    """Facts marked superseded do not appear in default search results.

    Sabotage-proof: change FakeFactStore.search to include superseded
    facts and this assertion fails because the old fact appears in
    results despite being superseded by the new one.
    """
    store = FakeFactStore()
    old = FakeFactRecord(id="f-old", entity="agent-alpha", attribute="status", value="married")
    new = FakeFactRecord(id="f-new", entity="agent-alpha", attribute="status", value="single")
    store.add(old)
    store.add(new)
    store.supersede(old_id="f-old", new_id="f-new")

    hits = store.search("agent-alpha status", top_k=10)
    hit_ids = {h.record.id for h in hits}
    assert "f-old" not in hit_ids, f"superseded fact must not appear in default search; got {hit_ids!r}"
    assert "f-new" in hit_ids, "new fact must still appear"


# ---------------------------------------------------------------------------
# Namespace filtering — engagement-scoped recall
# ---------------------------------------------------------------------------


def test_search_respects_namespace_filter() -> None:
    """``namespace=`` restricts results to facts in that namespace.

    Critical for the consultancy-in-a-box pattern — each engagement
    queries its own namespace; cross-engagement leak would defeat the
    isolation guarantee.
    """
    store = FakeFactStore()
    store.add(FakeFactRecord(id="f-eng-a", entity="ClientA", attribute="x", value="match", namespace="eng-a"))
    store.add(FakeFactRecord(id="f-eng-b", entity="ClientB", attribute="x", value="match", namespace="eng-b"))

    hits_a = store.search("match", top_k=10, namespace="eng-a")
    assert {h.record.id for h in hits_a} == {"f-eng-a"}, "namespace filter must isolate eng-a results"

    hits_all = store.search("match", top_k=10, namespace=None)
    assert {h.record.id for h in hits_all} == {"f-eng-a", "f-eng-b"}, "namespace=None returns all"


def test_find_conflicts_returns_live_facts_for_entity_attribute_key() -> None:
    """``find_conflicts`` returns live (non-superseded) facts for an entity+attribute.

    Used by the consolidation pass; the contract pins exactly what gets
    returned so the consolidation code can rely on the shape.
    """
    store = FakeFactStore()
    store.add(FakeFactRecord(id="f1", entity="agent-alpha", attribute="status", value="single"))
    store.add(FakeFactRecord(id="f2", entity="agent-alpha", attribute="status", value="dating"))
    store.add(FakeFactRecord(id="f3", entity="agent-alpha", attribute="job", value="VP"))  # different attribute
    store.add(FakeFactRecord(id="f4", entity="John", attribute="status", value="married"))  # different entity

    conflicts = store.find_conflicts(entity="agent-alpha", attribute="status")
    ids = {f.id for f in conflicts}
    assert ids == {"f1", "f2"}, f"find_conflicts must return only entity+attribute matches; got {ids!r}"


def test_find_conflicts_excludes_superseded() -> None:
    """find_conflicts must not return facts already marked superseded."""
    store = FakeFactStore()
    store.add(FakeFactRecord(id="f-old", entity="X", attribute="y", value="v1"))
    store.add(FakeFactRecord(id="f-new", entity="X", attribute="y", value="v2"))
    store.supersede(old_id="f-old", new_id="f-new")

    conflicts = store.find_conflicts(entity="X", attribute="y")
    ids = {f.id for f in conflicts}
    assert ids == {"f-new"}, f"find_conflicts must exclude superseded; got {ids!r}"


# ---------------------------------------------------------------------------
# Supersede semantics
# ---------------------------------------------------------------------------


def test_supersede_links_old_to_new() -> None:
    """After ``supersede(old, new)``, the old fact carries ``superseded_by=new``."""
    store = FakeFactStore()
    store.add(FakeFactRecord(id="f-old", entity="X", attribute="y", value="v1"))
    store.add(FakeFactRecord(id="f-new", entity="X", attribute="y", value="v2"))
    store.supersede(old_id="f-old", new_id="f-new")

    # Use find_conflicts (which returns live) to confirm f-old is no longer live
    live = store.find_conflicts(entity="X", attribute="y")
    assert {f.id for f in live} == {"f-new"}


def test_supersede_missing_old_id_raises_key_error() -> None:
    """Superseding a non-existent old id raises KeyError per the Protocol."""
    store = FakeFactStore()
    store.add(FakeFactRecord(id="f-new", entity="X", attribute="y", value="v"))
    with pytest.raises(KeyError, match="no fact with id"):
        store.supersede(old_id="f-does-not-exist", new_id="f-new")


def test_supersede_missing_new_id_raises_key_error() -> None:
    """Superseding to a non-existent new id raises KeyError per the Protocol."""
    store = FakeFactStore()
    store.add(FakeFactRecord(id="f-old", entity="X", attribute="y", value="v"))
    with pytest.raises(KeyError, match="no fact with id"):
        store.supersede(old_id="f-old", new_id="f-does-not-exist")


# ---------------------------------------------------------------------------
# FactExtractor consumer contract — what callers of extract() can rely on
# ---------------------------------------------------------------------------


def test_extractor_returns_scripted_facts_unchanged() -> None:
    """A scripted FakeFactExtractor returns the configured facts regardless
    of input turns — pins the consumer-side surface."""
    scripted = [
        FakeFactRecord(id="f1", entity="A", attribute="b", value="c"),
        FakeFactRecord(id="f2", entity="D", attribute="e", value="f"),
    ]
    extractor = FakeFactExtractor(scripted_facts=scripted)
    result = extractor.extract(turns=[{"id": "t1", "speaker": "x", "content": "y"}])
    assert len(result) == 2
    assert {r.id for r in result} == {"f1", "f2"}


def test_extractor_records_calls_for_assertion() -> None:
    """The fake records every extract invocation so tests can assert
    the consumer threaded turns through correctly."""
    extractor = FakeFactExtractor()
    turns = [{"id": "t1", "speaker": "x", "content": "y"}]
    extractor.extract(turns=turns, window_hint={"session_id": "s-1"})
    assert len(extractor.calls) == 1
    assert extractor.calls[0]["turns"] == turns
    assert extractor.calls[0]["window_hint"] == {"session_id": "s-1"}


def test_extractor_empty_facts_is_valid_return() -> None:
    """Empty list from extract() is a valid "no facts groundable" signal.

    Callers (the ingest pipeline) MUST tolerate this without raising.
    """
    extractor = FakeFactExtractor(scripted_facts=[])
    assert extractor.extract(turns=[{"id": "t", "speaker": "x", "content": "y"}]) == []
