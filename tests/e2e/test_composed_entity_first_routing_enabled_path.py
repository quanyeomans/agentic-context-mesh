"""F48-style composed-path E2E for entity-first routing (#429 Phase 2b).

Builds on the ADR-036 entity-summary indexing E2E: a real SQLite + FTS5
index, a real projector tick that lands an ``entity://`` chunk, then the
real ``factory.build_search_pipeline`` + real intent classifier. The only
injected seams are (1) a graph reporting ``available=True`` — ENTITY
intent requires a reachable graph and CI has no live Neo4j — and (2) the
``EntityFirstRoutingBoost`` flag driven via its ``flag_reader`` DI seam
(the factory reads the real resolver, which can't be redirected without
env/cwd mutation; the DI seam is the F2-clean equivalent).

Two scenarios:

1. **ON routes the entity first** — for "tell me about …" (ENTITY intent)
   the projected entity summary leads the results.
2. **OFF is a no-op** — same query, flag OFF, the plain vault note keeps
   the top spot. Pre-#429 ranking preserved.

Reuses the indexing scaffolding from the sibling entity-summary E2E so the
real projector → SQLite → FTS path is exercised once, not re-derived.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from kairix.core.factory import (
    FactoryDeps,
    build_search_pipeline,
    reset_search_pipeline_cache,
)
from kairix.core.search.boosts import EntityFirstRoutingBoost
from kairix.core.search.config import EntityFirstRoutingConfig, RetrievalConfig
from tests.e2e.test_composed_entity_summary_path import (
    _ScriptedNeo4jForE2E,
    _build_e2e_db,
    _result_paths,
    _run_projector_tick,
    _vault_root,
)
from tests.fakes import (
    FakeGraphRepository,
    FakePaths,
    FakeProvider,
    FakeProviderRegistry,
)

pytestmark = pytest.mark.e2e

_QUERY = "tell me about the AI policy and ethics research institute"
_NOTE = "vault_note.md"
# A vault note that matches the query terms so it is a genuine rival to the
# entity summary in the candidate set.
_NOTE_BODY = "# AI policy notes\nNotes on the AI policy and ethics research institute landscape.\n"
_ENTITY_ROW = {
    "name": "Ada Lovelace Institute",
    "qid": "Q1",
    "summary": "an AI policy and ethics research institute",
    "prior_hash": "",
    "summary_source": "wikidata",
}


def _build_routing_pipeline(*, db_path: Path, document_root: Path, flag_on: bool) -> Any:
    """Real factory pipeline (real classifier + DB-backed BM25/vector),
    with the entity-first routing boost driven ON / OFF via its DI seam."""
    paths = FakePaths(
        document_root=document_root,
        db_path=db_path,
        log_dir=db_path.parent / "logs",
        workspace_root=db_path.parent / "workspaces",
    )
    routing = EntityFirstRoutingBoost(
        config=EntityFirstRoutingConfig(factor=8.0),
        flag_reader=lambda: flag_on,
    )
    cfg = RetrievalConfig(provider="fake")
    registry = FakeProviderRegistry({"fake": FakeProvider(name="fake", vector=[0.1] * 1536, dim=1536)})
    reset_search_pipeline_cache()
    return build_search_pipeline(
        config=cfg,
        registry=registry,
        paths=paths,
        deps=FactoryDeps(
            graph_override=FakeGraphRepository(available=True),
            boosts_override=[routing],
        ),
    )


def _seed(tmp_path: Path) -> tuple[Path, Path]:
    """Real vault + SQLite/FTS + projected entity-summary chunk."""
    document_root = _vault_root(tmp_path)
    (document_root / _NOTE).write_text(_NOTE_BODY)
    db_path = _build_e2e_db(tmp_path, {_NOTE: _NOTE_BODY})
    neo4j = _ScriptedNeo4jForE2E([dict(_ENTITY_ROW)])
    projected, *_ = _run_projector_tick(db_path=db_path, neo4j=neo4j, flag_on=True)
    assert projected == 1, f"expected the projector to index 1 entity, got {projected}"
    return db_path, document_root


def test_composed_entity_first_routing_on_routes_entity_to_top(tmp_path: Path) -> None:
    """Flag ON + ENTITY-intent query → the entity summary leads the results.

    Sabotage-proof: drop the ``sorted(...)`` re-sort in
    ``EntityFirstRoutingBoost.boost`` (or the append in ``select_boosts``)
    and the entity row no longer reaches rank 1 here.
    """
    db_path, document_root = _seed(tmp_path)
    pipeline = _build_routing_pipeline(db_path=db_path, document_root=document_root, flag_on=True)

    result = pipeline.search(query=_QUERY, budget=3000)

    paths = _result_paths(result)
    assert paths, "composed pipeline returned no results"
    assert paths[0].startswith("entity://"), f"entity summary not routed first: {paths}"


def test_composed_entity_first_routing_off_keeps_note_on_top(tmp_path: Path) -> None:
    """Flag OFF → the plain vault note keeps the top spot (byte-for-byte
    pre-#429 ranking); the entity summary is not routed first."""
    db_path, document_root = _seed(tmp_path)
    pipeline = _build_routing_pipeline(db_path=db_path, document_root=document_root, flag_on=False)

    result = pipeline.search(query=_QUERY, budget=3000)

    paths = _result_paths(result)
    assert paths, "composed pipeline returned no results"
    assert not paths[0].startswith("entity://"), f"entity summary should not lead when flag OFF: {paths}"
