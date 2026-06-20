"""End-to-end composed path: the skills connector → run_recommend (F48).

This is the test that proves the production seam the final whole-branch
review found broken. The skills connector's REAL production worker path
(``run_connector_sync_pipeline`` → ``resolve_chunk_writer_for_entry(db,
name="skills")`` → ``legacy_chunk_writer(db, collection="skills")``) lands
its capability documents in the ``skills`` collection — named after the
connector by the connector framework, NOT in ``capabilities``. Before this
fix, ``run_recommend`` queried only ``collections=["capabilities"]``, so
every externally-installed skill was invisible to the recommender in
production. Every prior Feeder-2 test hand-wired ``collection="capabilities"``,
which hid the gap.

This test composes the genuinely production collection wiring:

  build_connector_pipeline(db=db, collection="skills")   # the REAL routing
    → real SkillsConnector walks a tmp ~/.claude tree, emits a skill
    → passthrough extractor → DefaultSilverProcessor → _SqliteChunkWriter
    → docs land in the ``skills`` collection
  build_search_pipeline(paths=…, skip_vector=True)        # BM25-only, no provider
  run_recommend(task, deps=RecommendDeps(search_fn=pipeline.search, …))
    → ranks over BOTH ``capabilities`` AND ``skills``
    → the seeded skill comes back as kind="skill", surface="external"

Without the fix (run_recommend querying only ``capabilities``) this test
FAILS: the ``skills``-collection doc is never queried, so no recommendation
returns. With the fix it passes — proving external skills are reachable.

F48 contract: file carries ``@pytest.mark.e2e``, runs in CI Stage 4.5 under
``pytest -m e2e``, and exercises real composition end-to-end (no fakes
hiding the collection-routing seam).

Sabotage proof (executed mutate -> fail -> restore): reverting
``_CAPABILITY_COLLECTIONS`` in ``kairix.use_cases.recommend`` back to a
single ``["capabilities"]`` query makes ``run_recommend`` skip the
``skills`` collection → ``out.recommendations`` is empty → the
``kind == "skill"`` assertion fails. Restoring the two-collection constant
turns it green. (Observed run reported by the agent.)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kairix.connectors.skills import SkillsConnector
from kairix.core.connectors import ExtractorRegistry
from kairix.core.db.schema import create_schema
from kairix.core.factory import (
    FactoryDeps,
    build_connector_pipeline,
    build_search_pipeline,
)

pytestmark = pytest.mark.e2e

# The REAL production collection the skills connector lands in — named
# after the connector by the framework. NOT "capabilities". This is the
# whole point of the test: the recommender must read this collection.
_SKILLS_COLLECTION = "skills"

# Distinctive token seeded into the skill body so a BM25 false-positive is
# structurally impossible in this in-memory test DB.
_QUERY_TOKEN = "radiator"


class _NullEmbed:
    """Provider-free embed service for the skip_vector BM25-only E2E path."""

    def embed(self, text: str) -> list[float]:
        del text
        return []

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[] for _ in texts]


def _seed_skill(claude_root: Path) -> None:
    skill = claude_root / "plugins/cache/mkt/sp/5.0.0/skills/heat-planning/SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text(
        "---\nname: heat-planning\ndescription: Plan room heating layouts.\n---\n"
        f"Use this skill to size a {_QUERY_TOKEN} for a room before any install work.\n",
        encoding="utf-8",
    )


def test_composed_skills_recommendation_path(tmp_path: Path) -> None:
    """skills connector (real ``skills`` collection) → run_recommend → kind=skill.

    Sabotage proof (executed): reverting ``_CAPABILITY_COLLECTIONS`` to the
    single ``["capabilities"]`` query → no skill recommendation returns →
    the ``kind == "skill"`` assertion fails. Restored → green.
    """
    from dataclasses import replace

    from kairix.core.search.config import RetrievalConfig
    from kairix.paths import KairixPaths
    from kairix.use_cases.recommend import (
        RecommendDeps,
        recommender_config,
        run_recommend,
    )

    # 1. Seed a tmp ~/.claude tree with one skill and run the connector
    #    through the REAL production collection ("skills"), not "capabilities".
    claude_root = tmp_path / ".claude"
    _seed_skill(claude_root)
    connector = SkillsConnector(claude_root=claude_root)

    db_path = tmp_path / "index.sqlite"
    db = sqlite3.connect(str(db_path), timeout=10.0)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    create_schema(db)

    registry = ExtractorRegistry()
    extractor = registry.resolve("text/markdown", b"# heat-planning")
    pipeline = build_connector_pipeline(db=db, collection=_SKILLS_COLLECTION)
    batch = pipeline.run_batch(connector, extractor)
    db.commit()
    db.close()
    assert batch.processed >= 1, (
        f"connector must index the seeded skill into the {_SKILLS_COLLECTION!r} collection; got {batch}"
    )

    # 2. Build a real factory SearchPipeline over the seeded index (BM25-only,
    #    provider-free) and call the real run_recommend through it.
    from kairix.core.factory import reset_search_pipeline_cache

    reset_search_pipeline_cache()
    cfg = replace(recommender_config(RetrievalConfig.defaults()), skip_vector=True)
    paths = KairixPaths(
        db_path=db_path,
        document_root=tmp_path,
        log_dir=tmp_path,
        workspace_root=tmp_path,
    )
    search_pipeline = build_search_pipeline(
        config=cfg,
        paths=paths,
        deps=FactoryDeps(embed_service_override=_NullEmbed()),
    )

    deps = RecommendDeps(
        search_fn=lambda *, query, collections, agent, **_kw: search_pipeline.search(
            query=query, collections=collections, agent=agent
        ),
        catalogue_fn=lambda: [],  # external skills need no kairix catalogue enrichment
        correlation_id_fn=lambda: "cid",
    )

    out = run_recommend(f"how do I size a {_QUERY_TOKEN}?", limit=5, deps=deps)

    assert out.error == ""
    assert out.recommendations, (
        "the recommender must rank the skills-connector output reachable in the "
        f"{_SKILLS_COLLECTION!r} collection; got no recommendations. This is the "
        "production seam the fix repairs — run_recommend must query BOTH "
        "'capabilities' and 'skills'."
    )
    skills = [r for r in out.recommendations if r.kind == "skill"]
    assert skills, f"expected a kind='skill' recommendation; got {[(r.name, r.kind) for r in out.recommendations]}"
    top_skill = skills[0]
    assert top_skill.name == "heat-planning", f"expected the seeded skill; got {top_skill.name!r}"
    assert top_skill.surface == "external", f"external skill must carry surface='external'; got {top_skill.surface!r}"
