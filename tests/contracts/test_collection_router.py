"""Contract tests for CollectionRouter (ADR v2 §5 chunk-write dispatch).

Pins:
* Most-specific filter wins when multiple mappings could match.
* ``on_unmapped_item='drop'`` increments the dropped counter and writes nothing.
* ``on_unmapped_item='land_in_default_collection'`` falls back to first mapping.
* Multi-cc_pair routing isolates per-cc_pair state.
* F61: ``_SqliteChunkWriter`` construction lives inside the framework
  (this contract test exercises it via the public API only).

Sabotage-prove targets:
- Most-specific routing: swap the sort to ascending → confirm
  test_most_specific_filter_wins fails → restore.
- Drop counter: change ``self._dropped += len(chunks)`` to
  ``self._dropped += 1`` → confirm
  test_on_unmapped_drop_increments_counter fails → restore.
"""

from __future__ import annotations

import sqlite3

import pytest

from kairix.core.connectors.collection_router import CollectionRouter
from kairix.core.db.schema import create_schema
from kairix.core.protocols import Chunk

pytestmark = pytest.mark.contract


def _fresh_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    create_schema(db, dims=4)
    return db


def _make_chunk(*, source_uri: str, text: str = "hello") -> Chunk:
    return Chunk(
        text=text,
        content_hash="hash-" + source_uri,
        source_name="src",
        source_uri=source_uri,
        source_modified_at="2026-05-23T00:00:00Z",
        source_page=None,
        sensitivity="internal",
    )


def _seed_cc_pair(db: sqlite3.Connection, *, name: str) -> int:
    now = "2026-05-23T00:00:00Z"
    cur = db.execute(
        "INSERT INTO topology_connectors "
        "(kind, name, connector_specific_config, default_sensitivity, created_at, updated_at) "
        "VALUES ('obsidian', ?, '{}', 'internal', ?, ?)",
        (f"{name}-conn", now, now),
    )
    connector_id = cur.lastrowid
    cur = db.execute(
        "INSERT INTO topology_cc_pairs "
        "(connector_id, credential_id, name, access_type, status, "
        "in_repeated_error_state, total_docs_indexed, created_at, updated_at) "
        "VALUES (?, NULL, ?, 'PRIVATE', 'ACTIVE', 0, 0, ?, ?)",
        (connector_id, name, now, now),
    )
    db.commit()
    assert cur.lastrowid is not None
    return int(cur.lastrowid)


def _seed_collection(
    db: sqlite3.Connection,
    *,
    name: str,
    on_unmapped_item: str = "land_in_default_collection",
) -> int:
    now = "2026-05-23T00:00:00Z"
    cur = db.execute(
        "INSERT INTO topology_collections "
        "(name, default_sensitivity, on_unmapped_item, visibility, created_at, updated_at) "
        "VALUES (?, 'internal', ?, 'engagement', ?, ?)",
        (name, on_unmapped_item, now, now),
    )
    db.commit()
    assert cur.lastrowid is not None
    return int(cur.lastrowid)


def _seed_mapping(db: sqlite3.Connection, *, collection_id: int, cc_pair_id: int, filter_glob: str) -> None:
    db.execute(
        "INSERT INTO topology_collection_sources "
        "(collection_id, cc_pair_id, source_path_filter, sensitivity_override) "
        "VALUES (?, ?, ?, NULL)",
        (collection_id, cc_pair_id, filter_glob),
    )
    db.commit()


def test_most_specific_filter_wins() -> None:
    """Two mappings on the same cc_pair: the longer filter wins."""
    db = _fresh_db()
    cc_pair_id = _seed_cc_pair(db, name="cc-alpha")
    broad = _seed_collection(db, name="broad")
    specific = _seed_collection(db, name="specific")
    _seed_mapping(db, collection_id=broad, cc_pair_id=cc_pair_id, filter_glob="*")
    _seed_mapping(
        db,
        collection_id=specific,
        cc_pair_id=cc_pair_id,
        filter_glob="01-Projects/Client-X/*",
    )

    router = CollectionRouter(db, cc_pair_id)
    chunk = _make_chunk(source_uri="01-Projects/Client-X/note.md")
    result = router.write_chunks("01-Projects/Client-X/note.md", [chunk])
    db.commit()
    assert result.collection_name == "specific"
    assert result.n_written == 1


def test_broader_filter_catches_when_specific_misses() -> None:
    """The broad mapping catches items the specific one rejects."""
    db = _fresh_db()
    cc_pair_id = _seed_cc_pair(db, name="cc-alpha")
    broad = _seed_collection(db, name="broad")
    specific = _seed_collection(db, name="specific")
    _seed_mapping(db, collection_id=broad, cc_pair_id=cc_pair_id, filter_glob="*")
    _seed_mapping(
        db,
        collection_id=specific,
        cc_pair_id=cc_pair_id,
        filter_glob="01-Projects/Client-X/*",
    )

    router = CollectionRouter(db, cc_pair_id)
    chunk = _make_chunk(source_uri="other/note.md")
    result = router.write_chunks("other/note.md", [chunk])
    db.commit()
    assert result.collection_name == "broad"


def test_on_unmapped_drop_increments_counter() -> None:
    """No mapping matches AND on_unmapped_item='drop' → counter increments, no write."""
    db = _fresh_db()
    cc_pair_id = _seed_cc_pair(db, name="cc-alpha")
    coll = _seed_collection(db, name="restrict", on_unmapped_item="drop")
    _seed_mapping(
        db,
        collection_id=coll,
        cc_pair_id=cc_pair_id,
        filter_glob="restricted/*",
    )

    router = CollectionRouter(db, cc_pair_id)
    assert router.dropped_count == 0
    chunks = [
        _make_chunk(source_uri="unmapped/a"),
        _make_chunk(source_uri="unmapped/b"),
    ]
    result = router.write_chunks("unmapped/a", chunks)
    assert result.collection_name is None
    assert result.n_written == 0
    assert result.on_unmapped_dropped == 2
    assert router.dropped_count == 2


def test_on_unmapped_lands_in_default_collection() -> None:
    """No mapping matches AND on_unmapped_item='land_in_default_collection' → default."""
    db = _fresh_db()
    cc_pair_id = _seed_cc_pair(db, name="cc-alpha")
    coll = _seed_collection(db, name="default-bucket", on_unmapped_item="land_in_default_collection")
    _seed_mapping(db, collection_id=coll, cc_pair_id=cc_pair_id, filter_glob="restricted/*")

    router = CollectionRouter(db, cc_pair_id)
    chunk = _make_chunk(source_uri="anywhere-else/note.md")
    result = router.write_chunks("anywhere-else/note.md", [chunk])
    db.commit()
    assert result.collection_name == "default-bucket"
    assert result.n_written == 1
    assert router.dropped_count == 0


def test_multi_cc_pair_isolation() -> None:
    """Two cc_pairs share a DB; each router sees only its own mappings."""
    db = _fresh_db()
    alpha = _seed_cc_pair(db, name="cc-alpha")
    beta = _seed_cc_pair(db, name="cc-beta")
    coll_a = _seed_collection(db, name="alpha-coll")
    coll_b = _seed_collection(db, name="beta-coll")
    _seed_mapping(db, collection_id=coll_a, cc_pair_id=alpha, filter_glob="*")
    _seed_mapping(db, collection_id=coll_b, cc_pair_id=beta, filter_glob="*")

    alpha_router = CollectionRouter(db, alpha)
    beta_router = CollectionRouter(db, beta)
    assert alpha_router.mapping_count() == 1
    assert beta_router.mapping_count() == 1
    alpha_result = alpha_router.write_chunks("x", [_make_chunk(source_uri="x")])
    beta_result = beta_router.write_chunks("y", [_make_chunk(source_uri="y")])
    db.commit()
    assert alpha_result.collection_name == "alpha-coll"
    assert beta_result.collection_name == "beta-coll"


def test_router_with_no_mappings_drops_everything() -> None:
    """A cc_pair with zero mappings drops every chunk (no default to land in)."""
    db = _fresh_db()
    cc_pair_id = _seed_cc_pair(db, name="lonely")
    router = CollectionRouter(db, cc_pair_id)
    assert router.mapping_count() == 0
    result = router.write_chunks("anything", [_make_chunk(source_uri="anything")])
    assert result.collection_name is None
    assert result.n_written == 0
    assert router.dropped_count == 1


def test_router_exposes_cc_pair_id() -> None:
    db = _fresh_db()
    cc_pair_id = _seed_cc_pair(db, name="cc-id-check")
    router = CollectionRouter(db, cc_pair_id)
    assert router.cc_pair_id == cc_pair_id
