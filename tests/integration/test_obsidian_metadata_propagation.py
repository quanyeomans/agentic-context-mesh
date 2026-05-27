"""Obsidian envelope metadata propagation — ADR-021 / F65.

The :class:`kairix.connectors.obsidian.ObsidianConnector` lifts file
mtime + frontmatter ``author:`` / ``tags:`` into the
:class:`SourceMetadata` envelope. The silver-merge layer threads that
envelope through to :class:`~kairix.core.protocols.Chunk` so
temporal-boost search and the entity graph see the right author /
modified-at for every Obsidian-indexed file.

Sabotage proof: drop the ``author:`` line from the vault fixture, run
the test, assert it fails because ``chunk.author`` is None; restore,
confirm it passes again.

Spec: ``docs/architecture/ADR-021-per-source-metadata-normalisation.md``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kairix.connectors.obsidian import ObsidianConnector
from kairix.core import factory
from kairix.core.db.schema import create_schema
from tests.fakes import FakeChunkWriter, FakeEntityGraphSink, FakeExtractor

pytestmark = pytest.mark.integration

_VAULT_FIXTURE = """---
author: agent-alpha
tags:
  - design
  - architecture
---

# Envelope-bearing note

Body paragraph carrying searchable text. This note's frontmatter is
the canonical Obsidian envelope shape ADR-021 expects every connector
to surface.
"""


def _open_db(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "obsidian_metadata.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    return db


def test_obsidian_envelope_metadata_lands_on_chunk(tmp_path: Path) -> None:
    """ObsidianConnector frontmatter + file stat populate Chunk.author + chunk_date + tags."""
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    note_path = vault_root / "envelope-note.md"
    note_path.write_text(_VAULT_FIXTURE, encoding="utf-8")

    connector = ObsidianConnector(vault_root=vault_root)
    db = _open_db(tmp_path)
    chunk_writer = FakeChunkWriter()
    pipeline = factory.build_connector_pipeline(
        db=db,
        collection="obsidian-metadata-propagation",
        chunk_writer=chunk_writer,
        entity_graph_sink=FakeEntityGraphSink(),
    )

    try:
        pipeline.run_batch(connector, FakeExtractor())
    finally:
        connector.close()

    chunks = [chunk for batch in chunk_writer.writes for chunk in batch]
    assert chunks, "ObsidianConnector did not surface any chunks"
    authors = [chunk.author for chunk in chunks]
    assert "agent-alpha" in authors, f"expected envelope author 'agent-alpha' on at least one chunk; got {authors!r}"
    chunk_dates = [chunk.source_modified_at for chunk in chunks]
    assert all(date for date in chunk_dates), (
        f"expected every chunk to carry an envelope-derived chunk_date; got {chunk_dates!r}"
    )
    all_tags: set[str] = set()
    for chunk in chunks:
        all_tags.update(chunk.tags)
    assert "design" in all_tags and "architecture" in all_tags, (
        f"expected frontmatter tags 'design' and 'architecture' on the chunk; got {sorted(all_tags)!r}"
    )
