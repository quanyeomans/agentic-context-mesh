"""Skills envelope metadata propagation — ADR-021 / F65.

:class:`kairix.connectors.skills.SkillsConnector` lifts the artefact's
file-stat envelope (mtime → chunk_date) plus the ``capability`` /
``kind:<k>`` tags onto the :class:`SourceMetadata` payload; silver
threads it through to the indexed :class:`~kairix.core.protocols.Chunk`.

F47 — the corpus + pipeline are constructed via
:func:`kairix.core.factory.build_connector_pipeline`; no direct
``*Pipeline(...)`` construction. The connector walks a ``tmp_path``-rooted
fake ``.claude`` tree (F32 — generic skill names, never the real
``~/.claude``).

Sabotage proof (executed by the agent, restored on completion): mutate
``SkillsConnector.metadata_for`` to return ``SourceMetadata()`` (drop the
tag lift); assert ``"capability" in chunk.tags`` becomes false; the test
fails; restore. (See Step 2.6 report for the observed run.)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kairix.connectors.skills import SkillsConnector
from kairix.core import factory
from kairix.core.db.schema import create_schema
from tests.fakes import FakeChunkWriter, FakeEntityGraphSink, FakeExtractor

pytestmark = pytest.mark.integration


def _seed_skill(root: Path) -> None:
    skill = root / "plugins/cache/mkt/sp/2.0.0/skills/brainstorming/SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text(
        "---\nname: brainstorming\ndescription: Explore the problem space first.\n---\nUse before any creative work.\n",
        encoding="utf-8",
    )


def test_skills_envelope_metadata_lands_on_chunk(tmp_path: Path) -> None:
    """SkillsConnector.metadata_for surfaces capability tags + chunk_date onto the chunk."""
    claude_root = tmp_path / ".claude"
    _seed_skill(claude_root)
    connector = SkillsConnector(claude_root=claude_root)

    db_path = tmp_path / "skills_metadata.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    chunk_writer = FakeChunkWriter()
    pipeline = factory.build_connector_pipeline(
        db=db,
        collection="capabilities",
        chunk_writer=chunk_writer,
        entity_graph_sink=FakeEntityGraphSink(),
    )

    pipeline.run_batch(connector, FakeExtractor())

    chunks = [chunk for batch in chunk_writer.writes for chunk in batch]
    assert chunks, "SkillsConnector did not surface any chunks"

    all_tags: set[str] = set()
    for chunk in chunks:
        all_tags.update(chunk.tags)
    assert "capability" in all_tags, f"expected 'capability' in chunk.tags; got {sorted(all_tags)!r}"
    assert "kind:skill" in all_tags, f"expected 'kind:skill' in chunk.tags; got {sorted(all_tags)!r}"

    # chunk_date rides source_modified_at (the artefact's file mtime envelope).
    chunk_dates = [chunk.source_modified_at for chunk in chunks]
    assert all(d for d in chunk_dates), f"expected populated chunk_date on every chunk; got {chunk_dates!r}"
