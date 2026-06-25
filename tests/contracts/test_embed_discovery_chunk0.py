"""Contract: the embed un-embedded discovery must surface EVERY chunk of a
multi-chunk connector doc, including chunk-0.

Regression for the chunk-0 embedding gap: ``_SqliteChunkWriter.upsert`` writes
a ``content_vectors(hash, seq=enumerate-index, pos=0)`` placeholder per chunk.
The discovery used to join on ``v.seq = 0``, which the placeholder for a doc's
first chunk (seq=0) satisfied — silently excluding chunk-0 from vector
embedding (BM25/FTS masked it). The fix keys the join on ``v.model IS NOT NULL``
("needs embedding = has no *embedded* vector"), so the placeholder no longer
hides any chunk and an already-embedded chunk is still not re-embedded.
"""

from __future__ import annotations

import hashlib
import sqlite3

import pytest

from kairix.core.connectors.collection_router import legacy_chunk_writer
from kairix.core.db.schema import create_schema
from kairix.core.embed.schema import get_pending_chunks
from kairix.core.protocols import Chunk

pytestmark = pytest.mark.contract

_SOURCE_URI = "sharepoint://site/multi-chunk-doc"


def _chunk(text: str, seq: int) -> Chunk:
    return Chunk(
        text=text,
        content_hash=hashlib.sha256(f"{seq}:{text}".encode()).hexdigest(),
        source_name="sharepoint",
        source_uri=_SOURCE_URI,
        source_modified_at="2026-06-25T00:00:00Z",
        source_page=None,
        sensitivity="internal",
        chunker_version="0.1.0",
    )


def _write_doc(db: sqlite3.Connection) -> list[Chunk]:
    chunks = [_chunk("First chunk body.", 0), _chunk("Second chunk body.", 1), _chunk("Third chunk body.", 2)]
    writer = legacy_chunk_writer(db, collection="default")
    writer.upsert(chunks)
    db.commit()
    return chunks


def test_every_chunk_including_index_0_is_discovered_for_embedding() -> None:
    db = sqlite3.connect(":memory:")
    create_schema(db)
    chunks = _write_doc(db)

    discovered = {row["hash"] for row in get_pending_chunks(db)}

    assert discovered == {c.content_hash for c in chunks}, (
        "every chunk (incl. chunk-0) must be discoverable for embedding; "
        f"missing: {sorted({c.content_hash for c in chunks} - discovered)}"
    )


def test_embedded_chunk_is_not_rediscovered_but_placeholders_still_are() -> None:
    db = sqlite3.connect(":memory:")
    create_schema(db)
    chunks = _write_doc(db)

    # Simulate the embed worker writing a real vector for chunk-1 (model set).
    db.execute(
        "INSERT OR REPLACE INTO content_vectors (hash, seq, pos, model, embedded_at) VALUES (?, 0, 0, ?, ?)",
        (chunks[1].content_hash, "text-embedding-3-large", "2026-06-25T01:00:00Z"),
    )
    db.commit()

    discovered = {row["hash"] for row in get_pending_chunks(db)}

    assert chunks[1].content_hash not in discovered, "an embedded chunk (model set) must not be re-embedded"
    assert discovered == {chunks[0].content_hash, chunks[2].content_hash}, (
        "the still-unembedded placeholders (incl. chunk-0) must remain discoverable"
    )
