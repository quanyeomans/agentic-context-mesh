"""Integration test — FactStore vector/RRF-fused recall (PLA-262, #340).

``FactStore.search`` was BM25-only: conversational queries that share no
lexical tokens with a stored fact returned nothing, even when the fact was
semantically the answer. PLA-262 wires the fused path — embed the query,
recall nearest-neighbour facts from the vector index, and RRF-fuse that
list with the BM25 list using the production search pipeline's
:func:`kairix.core.search.rrf.rrf` helper.

This test composes the real ``SQLiteFactStore`` (on a ``tmp_path`` SQLite
file) with a canonical ``FakeEmbeddingService`` + ``FakeVectorRepository``
from ``tests/fakes.py`` — the embedder and the fact vector index are the
two injection seams the production factory fills. It proves a
semantically-related fact with NO lexical overlap with the query is
recalled via the fused path, and that the SAME fact is MISSED by a
BM25-only store (the regression the fused path closes).

Marker rationale (``integration``): exercises multiple composed
components — the SQLite fact store, an embedding service, and a vector
index — through the public ``kairix.core.facts`` surface, against a real
on-disk SQLite database. (``SQLiteFactStore`` is not a ``*Pipeline``, so
F47's factory-construction rule does not apply; direct composition of the
store with its seams is the unit under test.)

Sabotage-proof (executed during authoring): reverting ``FactStore.search``
to BM25-only — i.e. ``return list(bm25_hits)`` before the vector leg runs —
makes ``test_fused_path_recalls_semantic_fact_bm25_misses`` fail because
``f-feline`` never reaches the result set. Transcript in the commit body.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kairix.core.facts import SQLiteFactStore, StoredFactRecord
from tests.fakes import FakeEmbeddingService, FakeVectorRepository

pytestmark = pytest.mark.integration


# Query and fact deliberately share ZERO tokens, so BM25 (FTS5 MATCH on
# entity/attribute/value) cannot surface the fact — only a semantic
# (vector) match can. "feline companions" vs "owns a tabby kitten".
_SEMANTIC_QUERY = "feline companions"


def _record(
    *,
    fact_id: str,
    entity: str,
    attribute: str,
    value: str,
    namespace: str = "shared",
    superseded_by: str | None = None,
) -> StoredFactRecord:
    return StoredFactRecord(
        id=fact_id,
        entity=entity,
        attribute=attribute,
        value=value,
        confidence=0.9,
        source_turn_ids=(f"t-{fact_id}",),
        extracted_at="2026-01-01T00:00:00Z",
        superseded_by=superseded_by,
        namespace=namespace,
    )


def test_bm25_only_store_misses_the_semantic_fact(tmp_path: Path) -> None:
    """Control: a store with no embedder/vector index cannot recall a fact
    that shares no tokens with the query — the regression PLA-262 closes.

    Sabotage-proof: this is the baseline the fused test is measured
    against; if BM25 *did* surface ``f-feline`` the fused assertion would
    be meaningless. Pinned so a tokeniser change that accidentally matched
    is caught.
    """
    store = SQLiteFactStore(db_path=tmp_path / "bm25_only.sqlite")
    store.add(_record(fact_id="f-feline", entity="agent-alpha", attribute="pet", value="owns a tabby kitten"))

    hits = store.search(_SEMANTIC_QUERY)

    assert all(h.record.id != "f-feline" for h in hits), (
        f"BM25-only must MISS the no-lexical-overlap fact; got {[h.record.id for h in hits]!r}"
    )


def test_fused_path_recalls_semantic_fact_bm25_misses(tmp_path: Path) -> None:
    """The fused path recalls a semantically-related fact that BM25 misses.

    The vector index (a ``FakeVectorRepository``) returns ``f-feline`` as
    the nearest neighbour for the embedded query; the fused store surfaces
    it even though the query shares no tokens with the fact.

    Sabotage-proof: revert ``search`` to ``return list(bm25_hits)`` (skip
    the vector leg) and ``f-feline`` is never recalled → this assertion
    fails. Executed during authoring.
    """
    store = SQLiteFactStore(
        db_path=tmp_path / "fused.sqlite",
        embedder=FakeEmbeddingService(vector=[0.1, 0.2, 0.3]),
        vector_index=FakeVectorRepository(results=[{"id": "f-feline", "collection": "facts"}]),
    )
    store.add(_record(fact_id="f-feline", entity="agent-alpha", attribute="pet", value="owns a tabby kitten"))

    hits = store.search(_SEMANTIC_QUERY)

    assert any(h.record.id == "f-feline" for h in hits), (
        "fused path must recall the semantically-related fact the vector "
        f"index returned; got {[h.record.id for h in hits]!r}"
    )


def test_fused_path_combines_bm25_and_vector_hits(tmp_path: Path) -> None:
    """Fusion is additive: a lexical BM25 hit AND a no-overlap vector hit
    both reach the result set in one fused list.

    The query lexically matches ``f-cat-lex`` (shares "cat"); the vector
    index also returns ``f-feline`` (no lexical overlap). Both must appear,
    proving RRF fused two distinct ranked lists rather than replacing one.

    Sabotage-proof: dropping the BM25 leg from the fusion (passing ``[]``
    for ``bm25_rows``) loses ``f-cat-lex``; skipping the vector leg loses
    ``f-feline``. Either mutation fails this test.
    """
    store = SQLiteFactStore(
        db_path=tmp_path / "combined.sqlite",
        embedder=FakeEmbeddingService(vector=[0.1, 0.2, 0.3]),
        vector_index=FakeVectorRepository(results=[{"id": "f-feline", "collection": "facts"}]),
    )
    store.add(_record(fact_id="f-cat-lex", entity="agent-alpha", attribute="cat", value="cat ownership noted"))
    store.add(_record(fact_id="f-feline", entity="agent-alpha", attribute="pet", value="owns a tabby kitten"))

    recalled = {h.record.id for h in store.search("cat")}

    assert "f-cat-lex" in recalled, f"BM25 lexical hit must survive fusion; got {recalled!r}"
    assert "f-feline" in recalled, f"vector-only hit must survive fusion; got {recalled!r}"
