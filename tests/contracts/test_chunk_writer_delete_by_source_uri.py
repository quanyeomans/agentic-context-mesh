"""Contract tests for the :meth:`ChunkWriter.delete_by_source_uri`
Protocol extension (ADR-036, #459 Slice A).

Exercises the new method against both the real ``_SqliteChunkWriter``
backed by a tmp SQLite DB and :class:`FakeChunkWriter`. The contract
pinned here is:

* deletion is keyed on ``source_uri`` + (implicitly) collection
* return value is the count of ``documents`` rows deleted (the
  production writer reads ``cursor.rowcount``; the fake returns the
  count of rows it had recorded under that URI)
* deletion is idempotent — calling twice returns ``N`` then ``0``
* FTS5 cleanup runs in lockstep on the real writer so BM25 retrieval
  no longer surfaces the deleted chunk's text
* a deleted URI can be re-upserted with new content (the re-projection
  path ADR-036 §Mechanics relies on)

F1/F2-clean; both targets are constructed through their canonical
seams.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kairix.core.db.schema import create_schema
from kairix.core.protocols import Chunk
from tests.fakes import FakeChunkWriter

pytestmark = pytest.mark.contract


def _seed_db(path: Path) -> sqlite3.Connection:
    """Build a tmp SQLite DB with the canonical kairix schema applied."""
    db = sqlite3.connect(str(path))
    create_schema(db)
    db.commit()
    return db


def _chunk(source_uri: str, text: str) -> Chunk:
    """Minimal Chunk satisfying F39 declared-public sensitivity."""
    return Chunk(
        text=text,
        content_hash=f"hash-{abs(hash((source_uri, text))) % 10_000_000}",
        source_name="wikidata",
        source_uri=source_uri,
        source_modified_at="2026-06-09T00:00:00Z",
        source_page=None,
        sensitivity="public",
        chunker_version="entity-summary:v1",
    )


# ---------------------------------------------------------------------------
# FakeChunkWriter — Protocol satisfier used in every other contract test
# ---------------------------------------------------------------------------


def test_fake_delete_by_source_uri_returns_zero_for_unknown_uri() -> None:
    """Deleting an unknown URI is a no-op + returns 0; idempotency
    holds on the empty-state branch."""
    writer = FakeChunkWriter()
    assert writer.delete_by_source_uri("entity://Q0") == 0


def test_fake_delete_by_source_uri_returns_count_after_upsert() -> None:
    """Upsert N chunks for one URI, then delete: count returned matches.

    Sabotage-proof: drop the ``self._by_uri.pop(...)`` line in the
    fake and a second delete still returns N instead of 0 — idempotency
    breaks.
    """
    writer = FakeChunkWriter()
    writer.upsert([_chunk("entity://Q1", "first"), _chunk("entity://Q1", "second")])
    assert writer.delete_by_source_uri("entity://Q1") == 2
    assert writer.delete_by_source_uri("entity://Q1") == 0
    assert writer.deletes == ["entity://Q1", "entity://Q1"]


def test_fake_delete_by_source_uri_isolates_distinct_uris() -> None:
    """Deleting one URI doesn't touch any other URI's tracked rows."""
    writer = FakeChunkWriter()
    writer.upsert([_chunk("entity://Q1", "first")])
    writer.upsert([_chunk("entity://Q2", "second")])
    assert writer.delete_by_source_uri("entity://Q1") == 1
    # Q2 still tracked — a subsequent delete still returns its count.
    assert writer.delete_by_source_uri("entity://Q2") == 1


# ---------------------------------------------------------------------------
# _SqliteChunkWriter — real production writer
# ---------------------------------------------------------------------------


def test_sqlite_delete_by_source_uri_returns_zero_for_unknown_uri(tmp_path: Path) -> None:
    """An unknown URI deletes zero rows; no exception, no commit
    discipline broken."""
    from kairix.core.connectors.collection_router import legacy_chunk_writer

    db = _seed_db(tmp_path / "kairix.db")
    writer = legacy_chunk_writer(db, collection="entity-summaries")
    deleted = writer.delete_by_source_uri("entity://Q-does-not-exist")
    assert deleted == 0


def test_sqlite_delete_by_source_uri_removes_document_and_fts_rows(tmp_path: Path) -> None:
    """The production happy path: upsert one chunk for an entity URI,
    then delete; the ``documents`` row is gone AND the paired
    ``documents_fts`` row is gone so BM25 no longer surfaces the text.

    Sabotage-proof: drop the ``DELETE FROM documents_fts`` loop in
    ``_SqliteChunkWriter.delete_by_source_uri`` and the FTS5 row
    assertion below fails (the orphan row keeps surfacing the deleted
    text).
    """
    from kairix.core.connectors.collection_router import legacy_chunk_writer

    db = _seed_db(tmp_path / "kairix.db")
    writer = legacy_chunk_writer(db, collection="entity-summaries")
    writer.upsert([_chunk("entity://Q42", "an unusual rare phrase")])
    db.commit()

    deleted = writer.delete_by_source_uri("entity://Q42")
    db.commit()

    assert deleted == 1
    doc_rows = db.execute("SELECT COUNT(*) FROM documents WHERE source_uri = ?", ("entity://Q42",)).fetchone()
    assert doc_rows[0] == 0
    fts_rows = db.execute(
        "SELECT COUNT(*) FROM documents_fts WHERE doc MATCH ?",  # F63-bounded: contract scope = one URI
        ("unusual",),
    ).fetchone()
    assert fts_rows[0] == 0


def test_sqlite_delete_by_source_uri_then_upsert_reprojects_clean(tmp_path: Path) -> None:
    """ADR-036 §Q6 re-projection path: delete + upsert leaves exactly
    one row carrying the new content. Locks the contract the
    EntitySummaryProjector relies on when a Wikidata description
    changes."""
    from kairix.core.connectors.collection_router import legacy_chunk_writer

    db = _seed_db(tmp_path / "kairix.db")
    writer = legacy_chunk_writer(db, collection="entity-summaries")
    writer.upsert([_chunk("entity://Q99", "outdated description")])
    db.commit()

    writer.delete_by_source_uri("entity://Q99")
    writer.upsert([_chunk("entity://Q99", "refreshed description")])
    db.commit()

    rows = db.execute(
        "SELECT doc FROM documents d JOIN content c ON c.hash = d.hash "  # F63-bounded: contract scope = one URI
        "WHERE d.source_uri = ?",
        ("entity://Q99",),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "refreshed description"


def test_sqlite_delete_by_source_uri_does_not_cross_collections(tmp_path: Path) -> None:
    """A delete on collection A leaves rows in collection B untouched
    even when the URI matches — F44 isolation contract."""
    from kairix.core.connectors.collection_router import legacy_chunk_writer

    db = _seed_db(tmp_path / "kairix.db")
    writer_a = legacy_chunk_writer(db, collection="entity-summaries")
    writer_b = legacy_chunk_writer(db, collection="vault-canon")
    writer_a.upsert([_chunk("entity://Q1", "from A")])
    writer_b.upsert([_chunk("entity://Q1", "from B")])
    db.commit()

    deleted = writer_a.delete_by_source_uri("entity://Q1")
    db.commit()

    assert deleted == 1
    remaining = db.execute("SELECT collection FROM documents WHERE source_uri = ?", ("entity://Q1",)).fetchall()
    assert [r[0] for r in remaining] == ["vault-canon"]
