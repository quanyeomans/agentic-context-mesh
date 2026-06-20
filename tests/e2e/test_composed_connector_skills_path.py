"""End-to-end composed path test for the ``connector_skills`` flag.

F48 sibling to ``tests/e2e/test_composed_production_path.py``. Pinned by
F54 because the flag's ``related_spec`` references
``docs/architecture/connector-ingestion-architecture.md`` — a top-level
capability spec.

Scenario: composes the full production path:

  build_connector_pipeline (factory)
    → real SkillsConnector walks a tmp_path fake ~/.claude tree, emits a
      ChangeEvent for a skill artefact with mime ``text/markdown``
    → ExtractorRegistry resolves the passthrough extractor (Markdown
      passes through unchanged)
    → DefaultSilverProcessor chunks the Markdown
    → _SqliteChunkWriter persists into documents + FTS5
    → BM25 query against the capabilities collection returns the chunk
      for a token from the skill body

Flag dispatch path:

  flag-resolver pins connector_skills=True
    → dispatch_skills_sync routes to the production ON branch helper
    → branch helper wraps the pipeline run

The OFF path is covered by the integration tests at
``tests/integration/test_feature_flag_connector_skills.py``. F54's E2E
requirement is per-flag (one E2E composed-path file).

No optional extras required — the skills connector renders artefacts as
Markdown, which routes through the passthrough extractor and avoids the
markitdown/pptx/docx skipif gates.

Sabotage proof (executed by the agent, restored on completion): mutating
the seeded SKILL.md body to drop the ``radiator`` token makes the BM25
assertion fail with ``AssertionError: 0 >= 1``. Restored, the distinctive
token surfaces via BM25. (See Step 2.6 report for the observed run.)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kairix.connectors.skills import SkillsConnector
from kairix.core.connectors import ExtractorRegistry
from kairix.core.db.schema import create_schema
from kairix.core.factory import build_connector_pipeline
from kairix.worker import (
    ConnectorSyncResult,
    dispatch_skills_sync,
)
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.e2e

# Distinctive token seeded into the skill body. ``radiator`` is a word not
# found in any fixture or framework prose, so a false-positive BM25 hit is
# structurally impossible in this in-memory test DB.
_QUERY_TOKEN = "radiator"


def _seed_skill(claude_root: Path) -> None:
    skill = claude_root / "plugins/cache/mkt/sp/5.0.0/skills/heat-planning/SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text(
        "---\nname: heat-planning\ndescription: Plan room heating layouts.\n---\n"
        "Use this skill to size a radiator for a room before any install work.\n",
        encoding="utf-8",
    )


def _build_db_with_schema(db_path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(str(db_path), timeout=10.0)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    create_schema(db, dims=4)
    return db


def test_composed_skills_capability_path(tmp_path: Path) -> None:
    """Skills: composed path artefact → connector → passthrough → silver → BM25.

    Sabotage proof (executed): dropped the ``radiator`` token from the
    seeded SKILL.md body → BM25 asserts ``0 >= 1`` (FAILED). Restored the
    token → 1 passed.
    """
    resolver = FakeFeatureFlagResolver().with_flag("connector_skills", True)

    claude_root = tmp_path / ".claude"
    _seed_skill(claude_root)
    connector = SkillsConnector(claude_root=claude_root)

    db_path = tmp_path / "index.sqlite"
    db = _build_db_with_schema(db_path)
    registry = ExtractorRegistry()

    def _on_branch() -> ConnectorSyncResult:
        # Resolve the passthrough extractor for text/markdown — the same
        # mime SkillsConnector.fetch() emits.
        extractor = registry.resolve("text/markdown", b"# heat-planning")
        pipeline = build_connector_pipeline(
            db=db,
            collection="capabilities",
        )
        result = pipeline.run_batch(connector, extractor)
        db.commit()
        return ConnectorSyncResult(
            synced=result.processed,
            failed=result.dead_lettered,
            dead_letter_added=result.dead_lettered,
        )

    sync_result = dispatch_skills_sync(
        read_flag=resolver.get,
        on_branch=_on_branch,
    )
    assert sync_result.synced >= 1, (
        f"composed path must index the seeded skill; got {sync_result}. "
        "Sabotage hint: check that SkillsConnector walked the tmp tree and the "
        "pipeline's chunk_writer received processed chunks."
    )

    rows = list(
        db.execute(
            "SELECT d.path FROM documents d JOIN documents_fts fts ON fts.rowid = d.id "
            "WHERE documents_fts MATCH ? AND d.collection = 'capabilities'",
            (_QUERY_TOKEN,),
        )
    )
    db.close()
    assert len(rows) >= 1, (
        "composed skills path must surface the skill body via BM25; got 0 matches. "
        f"Sabotage hint: check that the skill body contained {_QUERY_TOKEN!r} and "
        "the chunk-writer populated documents/content for the 'capabilities' collection."
    )
    # Close the end-to-end URI loop: the searchable doc must carry the
    # capability://skill/<name> source URI, proving the connector's source_link
    # shape survives the whole compose path (not just that *some* doc matched).
    paths = [p for (p,) in rows]
    assert any(p.startswith("capability://skill/heat-planning") for p in paths), (
        f"expected a capability://skill/heat-planning document; got {paths}"
    )
