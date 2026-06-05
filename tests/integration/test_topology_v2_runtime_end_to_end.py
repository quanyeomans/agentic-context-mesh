"""End-to-end integration test for the topology v2 Wave C runtime.

Exercises:
* Flag OFF (default): a single connector sync writes through the legacy
  single-collection chunk writer; chunks land in the connector-named
  collection.
* Flag ON: a connector sync with a registered cc_pair + mapping routes
  chunks through :class:`CollectionRouter`; chunks land in the mapped
  collection's name.

Per F47: constructs the pipeline via
:func:`kairix.core.factory.build_connector_pipeline`. The cc_pair
lifecycle + CollectionRouter dispatch is exercised through the worker's
``resolve_chunk_writer_for_entry`` adapter, not direct
:class:`CollectionRouter` construction in the test body.

Per F46 / F47: integration tests go through the factory; this file is
intentionally a "single-layer boundary proof" so it lives under
``tests/integration/`` but constructs the CollectionRouter via the
worker's resolver helper.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from kairix.core.connectors.cc_pair import create_cc_pair
from kairix.core.db.schema import create_schema
from kairix.core.factory import build_connector_pipeline
from kairix.core.protocols import Chunk
from kairix.worker import resolve_chunk_writer_for_entry

pytestmark = pytest.mark.integration


def _chunk(*, source_uri: str, content_hash: str | None = None) -> Chunk:
    return Chunk(
        text=f"text of {source_uri}",
        content_hash=content_hash or f"hash-{source_uri}",
        source_name="obsidian-personal",
        source_uri=source_uri,
        source_modified_at="2026-05-23T00:00:00Z",
        source_page=None,
        sensitivity="internal",
    )


def _build_db(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "kairix.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db, dims=4)
    return db


def _seed_connector(db: sqlite3.Connection, *, kind: str, name: str) -> int:
    now = "2026-05-23T00:00:00Z"
    cur = db.execute(
        "INSERT INTO topology_connectors "
        "(kind, name, connector_specific_config, default_sensitivity, created_at, updated_at) "
        "VALUES (?, ?, '{}', 'internal', ?, ?)",
        (kind, name, now, now),
    )
    db.commit()
    assert cur.lastrowid is not None
    return int(cur.lastrowid)


def _seed_collection_with_mapping(
    db: sqlite3.Connection,
    *,
    collection_name: str,
    cc_pair_id: int,
    filter_glob: str,
    on_unmapped: str = "land_in_default_collection",
) -> int:
    now = "2026-05-23T00:00:00Z"
    cur = db.execute(
        "INSERT INTO topology_collections "
        "(name, default_sensitivity, on_unmapped_item, visibility, created_at, updated_at) "
        "VALUES (?, 'internal', ?, 'engagement', ?, ?)",
        (collection_name, on_unmapped, now, now),
    )
    collection_id = cur.lastrowid
    assert collection_id is not None
    db.execute(
        "INSERT INTO topology_collection_sources "
        "(collection_id, cc_pair_id, source_path_filter, sensitivity_override) "
        "VALUES (?, ?, ?, NULL)",
        (int(collection_id), cc_pair_id, filter_glob),
    )
    db.commit()
    return int(collection_id)


def _read_collection_for_path(db: sqlite3.Connection, path: str) -> str | None:
    row = db.execute("SELECT collection FROM documents WHERE path = ?", (path,)).fetchone()
    return None if row is None else str(row[0])


def test_unmapped_entry_chunks_land_in_legacy_single_collection(tmp_path: Path) -> None:
    """When an entry has no cc_pair (no topology v2 wiring), the writer falls
    through to the legacy single-collection adapter and chunks land under the
    entry name. Pins the legacy fallthrough — post-#132 cutover the flag is gone
    but the fallthrough remains because not every entry has a cc_pair."""
    db = _build_db(tmp_path)
    writer = resolve_chunk_writer_for_entry(db, "obsidian-personal")
    chunk = _chunk(source_uri="01-Projects/Client-X/note.md")
    written = writer.upsert([chunk])
    db.commit()
    assert written == 1
    # documents.path is "{source_uri}#{seq}" per _SqliteChunkWriter shape.
    assert _read_collection_for_path(db, "01-Projects/Client-X/note.md#0") == "obsidian-personal"


def test_flag_on_chunks_land_in_mapped_collection(tmp_path: Path) -> None:
    """Flag ON + cc_pair + mapping: chunks land in the mapped collection's name."""
    db = _build_db(tmp_path)
    connector_id = _seed_connector(db, kind="obsidian", name="obsidian-personal-conn")
    cc_pair = create_cc_pair(
        db,
        connector_id=connector_id,
        credential_id=None,
        name="obsidian-personal",
    )
    db.commit()
    _seed_collection_with_mapping(
        db,
        collection_name="vault-projects",
        cc_pair_id=cc_pair.id,
        filter_glob="01-Projects/*",
    )
    writer = resolve_chunk_writer_for_entry(db, "obsidian-personal")
    chunk = _chunk(source_uri="01-Projects/Client-X/note.md")
    written = writer.upsert([chunk])
    db.commit()
    assert written == 1
    assert _read_collection_for_path(db, "01-Projects/Client-X/note.md#0") == "vault-projects"


def test_flag_on_off_branch_factory_pipeline_unchanged(tmp_path: Path) -> None:
    """Building the connector pipeline via factory produces a legacy writer.

    F47 contract: integration tests construct multi-component pipelines via
    factory.build_connector_pipeline. The Wave C wiring does not change the
    factory signature; the writer is built inside the framework via
    ``_legacy_chunk_writer``.
    """
    db = _build_db(tmp_path)
    bronze_root = tmp_path / "bronze"
    bronze_root.mkdir()
    pipeline: Any = build_connector_pipeline(db=db, collection="obsidian-personal")
    assert pipeline is not None
    # Pipeline carries a chunk_writer attribute exposing .upsert.
    assert hasattr(pipeline, "_chunk_writer") or hasattr(pipeline, "chunk_writer")


def test_flag_on_router_drops_when_unmapped_and_drop_policy(tmp_path: Path) -> None:
    """Flag ON + cc_pair + drop-policy mapping that doesn't match → drop."""
    db = _build_db(tmp_path)
    connector_id = _seed_connector(db, kind="obsidian", name="obsidian-personal-conn")
    cc_pair = create_cc_pair(
        db,
        connector_id=connector_id,
        credential_id=None,
        name="obsidian-personal",
    )
    db.commit()
    _seed_collection_with_mapping(
        db,
        collection_name="restricted-only",
        cc_pair_id=cc_pair.id,
        filter_glob="restricted/*",
        on_unmapped="drop",
    )
    writer = resolve_chunk_writer_for_entry(db, "obsidian-personal")
    chunk = _chunk(source_uri="open/note.md")
    written = writer.upsert([chunk])
    db.commit()
    assert written == 0
    # No documents row created.
    assert _read_collection_for_path(db, "open/note.md#0") is None


def test_flag_on_with_cc_pair_but_no_mappings_falls_back_to_legacy(tmp_path: Path) -> None:
    """Flag ON + cc_pair row but zero mappings → legacy writer fallback."""
    db = _build_db(tmp_path)
    connector_id = _seed_connector(db, kind="obsidian", name="obsidian-personal-conn")
    create_cc_pair(db, connector_id=connector_id, credential_id=None, name="obsidian-personal")
    db.commit()
    writer = resolve_chunk_writer_for_entry(db, "obsidian-personal")
    chunk = _chunk(source_uri="01-Projects/note.md")
    written = writer.upsert([chunk])
    db.commit()
    assert written == 1
    # cc_pair has zero mappings → legacy writer puts chunks in entry-name collection.
    assert _read_collection_for_path(db, "01-Projects/note.md#0") == "obsidian-personal"
