"""Tests for the GH #343 §10.7 canonical-skip patch in ``seed_graph``.

Without this skip, the kairix worker tick's regex-based entity scanner
(``scan_for_entities``) would regenerate the ~74 minimal-property
nodes that the iter_5 cypher-shell deployment had just cleansed —
within 24h of the cleanse, the production graph would drift back to
its pre-cleanse shape.

The patch reads canonical slugs from Neo4j once per :func:`seed_graph`
call (nodes carrying ``wikidata_qid`` or ``kairix_provenance_batch``),
then skips candidates whose ``suggested_id`` matches a canonical slug.
"""

from __future__ import annotations

import pytest

from kairix.knowledge.entities.seed import EntityCandidate, seed_graph

pytestmark = pytest.mark.unit


class _FakeNeo4jClient:
    """Minimal Neo4j client fake for seed_graph tests.

    Records every ``upsert_node`` call so tests can assert which
    candidates were written vs skipped. ``cypher(...)`` returns a
    canned canonical-slug rowset; ``available`` is True by default.
    """

    def __init__(self, canonical_slugs: set[str] | None = None) -> None:
        self.available = True
        self._canonical = canonical_slugs or set()
        self.upserts: list[tuple[str, str, dict]] = []

    def cypher(self, query: str, params: dict | None = None) -> list[dict]:
        # The seed canonical-fetch query asks for n.id WHERE
        # wikidata_qid OR kairix_provenance_batch is set. Return the
        # canned set as ``[{"id": slug}, ...]``.
        return [{"id": slug} for slug in self._canonical]

    def upsert_node(self, label: str, node_id: str, props: dict) -> bool:
        self.upserts.append((label, node_id, props))
        return True


def test_seed_graph_skips_candidates_matching_canonical_slug() -> None:
    """A candidate whose ``suggested_id`` matches a canonical slug is
    skipped — the upsert_node call MUST NOT happen for that candidate.

    Sabotage proof (the §10.7 regression): remove the
    ``if c.suggested_id in canonical_slugs: continue`` guard from
    seed_graph; the test fails because ``upserts`` carries 2 entries
    instead of 1 and ``bupa`` reappears.
    """
    client = _FakeNeo4jClient(canonical_slugs={"bupa"})
    candidates = [
        EntityCandidate(name="Bupa", entity_type="Organisation", confidence=0.85),
        EntityCandidate(name="Acme Corp", entity_type="Organisation", confidence=0.85),
    ]

    written_count = seed_graph(client, candidates)

    assert written_count == 1, f"only Acme should be written; Bupa is canonical. Got {written_count} writes."
    assert len(client.upserts) == 1
    assert client.upserts[0][1] == "acme-corp"
    # Bupa MUST NOT have been touched
    assert all(slug != "bupa" for (_label, slug, _props) in client.upserts)


def test_seed_graph_writes_when_no_canonical_match() -> None:
    """All candidates pass through when no canonical slug exists for
    them. Confirms the skip is opt-in (matches present) and doesn't
    over-fire.

    Sabotage proof: invert the skip to ``if c.suggested_id not in
    canonical_slugs: continue``; the test fails because both
    candidates are dropped.
    """
    client = _FakeNeo4jClient(canonical_slugs={"unrelated-org"})
    candidates = [
        EntityCandidate(name="Bupa", entity_type="Organisation", confidence=0.85),
        EntityCandidate(name="Acme Corp", entity_type="Organisation", confidence=0.85),
    ]

    written_count = seed_graph(client, candidates)
    assert written_count == 2


def test_seed_graph_short_circuits_when_neo4j_unavailable() -> None:
    """``available=False`` returns 0 without calling ``cypher`` or
    ``upsert_node`` — degraded mode is silent + safe.
    """
    client = _FakeNeo4jClient()
    client.available = False
    candidates = [EntityCandidate(name="Bupa", entity_type="Organisation", confidence=0.85)]

    written_count = seed_graph(client, candidates)
    assert written_count == 0
    assert client.upserts == []
