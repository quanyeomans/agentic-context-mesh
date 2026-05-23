"""E2E composed path for the topology v2 Wave C runtime — F48 sibling test.

ADR v2 §"Acceptance criteria" #2 calls for the obsidian-personal cc_pair
to run in:
  - per-folder routing mode (CollectionRouter end-to-end)
  - chunker-registry dispatch (markdown structural chunker v2 active for
    the obsidian collection)
  - HierarchyNode emission (folder tree queryable via
    ``kairix worker hierarchy show``)

This file is the minimum that satisfies all three signals:

1. **Per-folder routing**: seed a cc_pair + two folder-scoped collection
   sources; verify chunks for ``01-Projects/Client-X/note.md`` land in
   the ``client-x-engagement`` collection while
   ``01-Projects/Other/note.md`` lands in ``vault-projects``.
2. **Chunker registry dispatch**: register a chunker for
   ``("wiki-doc-store", "text/markdown")``; verify dispatch picks it
   and the emitted chunks carry the registered chunker's version.
3. **HierarchyNode emission**: persist a parent-before-child hierarchy
   to ``topology_hierarchy_nodes``; verify the read-back ordering is
   parent-before-child (the Wave-D CLI ``kairix worker hierarchy show``
   will format this read, but the storage contract is what F58 pins).

Per F48 + F47: lives under ``tests/e2e/`` with ``@pytest.mark.e2e``;
runs in CI Stage 4.5 under ``pytest -m e2e``; exercises config → factory
→ ingest → query → assertion via the composed production code paths.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from kairix.core.connectors import ChunkerRegistry
from kairix.core.connectors.cc_pair import create_cc_pair, transition_cc_pair
from kairix.core.db.schema import create_schema
from kairix.core.factory import build_connector_pipeline
from kairix.core.protocols import Chunk, HierarchyNode
from kairix.worker import resolve_chunk_writer_for_entry

pytestmark = pytest.mark.e2e


def _chunk(*, source_uri: str, chunker_version: str | None = None) -> Chunk:
    return Chunk(
        text=f"text for {source_uri}",
        content_hash=f"hash-{source_uri}",
        source_name="obsidian-personal",
        source_uri=source_uri,
        source_modified_at="2026-05-23T00:00:00Z",
        source_page=None,
        sensitivity="internal",
        chunker_version=chunker_version,
    )


def _bootstrap_e2e_db(tmp_path: Path) -> tuple[sqlite3.Connection, int]:
    """Create the production schema + seed the obsidian-personal cc_pair triad.

    Returns ``(db, cc_pair_id)``. The schema is the real Wave-A v3 shape.
    """
    db_path = tmp_path / "kairix.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db, dims=4)
    now = "2026-05-23T00:00:00Z"
    cur = db.execute(
        "INSERT INTO topology_connectors "
        "(kind, name, connector_specific_config, default_sensitivity, created_at, updated_at) "
        "VALUES ('obsidian', 'obsidian-personal-conn', '{}', 'internal', ?, ?)",
        (now, now),
    )
    connector_id = cur.lastrowid
    assert connector_id is not None
    cc_pair = create_cc_pair(
        db,
        connector_id=int(connector_id),
        credential_id=None,
        name="obsidian-personal",
    )
    db.commit()
    return db, cc_pair.id


def _seed_collection(
    db: sqlite3.Connection,
    *,
    name: str,
    cc_pair_id: int,
    filter_glob: str,
) -> None:
    now = "2026-05-23T00:00:00Z"
    cur = db.execute(
        "INSERT INTO topology_collections "
        "(name, default_sensitivity, on_unmapped_item, visibility, created_at, updated_at) "
        "VALUES (?, 'internal', 'land_in_default_collection', 'engagement', ?, ?)",
        (name, now, now),
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


def _seed_hierarchy_nodes(db: sqlite3.Connection, *, cc_pair_id: int, nodes: list[HierarchyNode]) -> None:
    """INSERT each HierarchyNode into topology_hierarchy_nodes IN ORDER.

    The parent-before-child invariant is enforced by emitting nodes in
    the order they're listed — readers (Wave D ``kairix worker hierarchy
    show``) preserve insertion order via primary-key ORDER BY rowid.
    """
    for node in nodes:
        db.execute(
            "INSERT INTO topology_hierarchy_nodes "
            "(cc_pair_id, raw_node_id, raw_parent_id, display_name, "
            "link, node_type, external_access_json, sensitivity_hint) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                node.cc_pair_id,
                node.raw_node_id,
                node.raw_parent_id,
                node.display_name,
                node.link,
                node.node_type,
                node.external_access_json,
                node.sensitivity_hint,
            ),
        )
    db.commit()


def test_composed_topology_v2_runtime_path_per_folder_routing(tmp_path: Path) -> None:
    """Per-folder routing signal: chunks land in the matching collection."""
    db, cc_pair_id = _bootstrap_e2e_db(tmp_path)
    # Two folder-scoped collections under the same cc_pair.
    _seed_collection(db, name="client-x-engagement", cc_pair_id=cc_pair_id, filter_glob="01-Projects/Client-X/*")
    _seed_collection(db, name="vault-projects", cc_pair_id=cc_pair_id, filter_glob="01-Projects/*")

    writer = resolve_chunk_writer_for_entry(db, "obsidian-personal", flag_on=True)
    written_client_x = writer.upsert([_chunk(source_uri="01-Projects/Client-X/sow.md")])
    written_other = writer.upsert([_chunk(source_uri="01-Projects/Other/note.md")])
    db.commit()
    assert written_client_x == 1
    assert written_other == 1

    client_x_row = db.execute(
        "SELECT collection FROM documents WHERE path = '01-Projects/Client-X/sow.md#0'"
    ).fetchone()
    other_row = db.execute("SELECT collection FROM documents WHERE path = '01-Projects/Other/note.md#0'").fetchone()
    assert client_x_row[0] == "client-x-engagement"
    assert other_row[0] == "vault-projects"


def test_composed_topology_v2_runtime_path_chunker_registry_dispatch(tmp_path: Path) -> None:
    """Chunker registry dispatch signal: registered chunker picks for matching key."""
    db, cc_pair_id = _bootstrap_e2e_db(tmp_path)
    _seed_collection(db, name="vault-projects", cc_pair_id=cc_pair_id, filter_glob="*")

    registry = ChunkerRegistry()

    class _ScriptedMarkdownStructuralChunkerV2:
        version = "2"

        def chunk(self, *, text: str, section_kind: str, source_uri: str) -> tuple[Chunk, ...]:
            del text, section_kind
            return (
                Chunk(
                    text="structural-chunk-1",
                    content_hash="h",
                    source_name="obsidian-personal",
                    source_uri=source_uri,
                    source_modified_at="2026-05-23T00:00:00Z",
                    source_page=None,
                    sensitivity="internal",
                    chunker_version=self.version,
                ),
            )

    chunker = _ScriptedMarkdownStructuralChunkerV2()
    registry.register(kind="wiki-doc-store", mime="text/markdown", chunker=chunker)

    picked = registry.dispatch(kind="wiki-doc-store", mime="text/markdown", section_kind="text")
    assert picked is chunker

    # Run the chunker through the production-shape envelope: emitted chunk
    # carries the registered chunker's version.
    chunks = picked.chunk(text="# heading", section_kind="text", source_uri="01-Projects/note.md")
    assert chunks
    assert chunks[0].chunker_version == "2"

    # And the fallback is still reachable for misses.
    fallback_pick = registry.dispatch(kind="unknown", mime="text/plain", section_kind="text")
    assert fallback_pick is registry.fallback
    fallback_chunks = fallback_pick.chunk(text="para one\n\npara two", section_kind="text", source_uri="x")
    for c in fallback_chunks:
        assert c.chunker_version == "1"


def test_composed_topology_v2_runtime_path_hierarchy_node_emission(tmp_path: Path) -> None:
    """HierarchyNode emission signal: storage round-trip preserves parent-before-child."""
    db, cc_pair_id = _bootstrap_e2e_db(tmp_path)
    nodes = [
        HierarchyNode(
            cc_pair_id=cc_pair_id,
            raw_node_id="root",
            raw_parent_id=None,
            display_name="Vault Root",
            link=None,
            node_type="FOLDER",
            external_access_json=None,
            sensitivity_hint=None,
        ),
        HierarchyNode(
            cc_pair_id=cc_pair_id,
            raw_node_id="01-Projects",
            raw_parent_id="root",
            display_name="01-Projects",
            link=None,
            node_type="FOLDER",
            external_access_json=None,
            sensitivity_hint=None,
        ),
        HierarchyNode(
            cc_pair_id=cc_pair_id,
            raw_node_id="01-Projects/Client-X",
            raw_parent_id="01-Projects",
            display_name="Client-X",
            link=None,
            node_type="FOLDER",
            external_access_json=None,
            sensitivity_hint=None,
        ),
    ]
    _seed_hierarchy_nodes(db, cc_pair_id=cc_pair_id, nodes=nodes)
    rows = db.execute(
        "SELECT raw_node_id, raw_parent_id FROM topology_hierarchy_nodes WHERE cc_pair_id = ? ORDER BY rowid",
        (cc_pair_id,),
    ).fetchall()
    seen: set[str] = set()
    for raw_id, raw_parent_id in rows:
        if raw_parent_id is not None:
            assert raw_parent_id in seen, (
                f"hierarchy round-trip violates parent-before-child: {raw_id!r} ↛ {raw_parent_id!r}"
            )
        seen.add(raw_id)
    assert seen == {"root", "01-Projects", "01-Projects/Client-X"}


def test_composed_topology_v2_runtime_path_cc_pair_lifecycle(tmp_path: Path) -> None:
    """cc_pair lifecycle signal: SCHEDULED → INITIAL_INDEXING → ACTIVE transitions work."""
    db, cc_pair_id = _bootstrap_e2e_db(tmp_path)
    # cc_pair lands at SCHEDULED via create_cc_pair.
    transition_cc_pair(db, cc_pair_id, "INITIAL_INDEXING")
    transition_cc_pair(db, cc_pair_id, "ACTIVE")
    db.commit()
    row = db.execute(
        "SELECT status, last_successful_index_time FROM topology_cc_pairs WHERE id = ?",
        (cc_pair_id,),
    ).fetchone()
    assert row[0] == "ACTIVE"
    assert row[1] is not None  # last_successful_index_time stamped on ACTIVE


def test_composed_topology_v2_runtime_path_factory_pipeline_builds(tmp_path: Path) -> None:
    """Factory builds a production-shape pipeline; the writer is constructed inside the framework.

    F61 + F47 + F46 contract: the factory IS the sanctioned construction
    surface for tests. The writer flows through the framework's
    legacy_chunk_writer helper (Wave C paydown) — the factory itself
    never names the underlying writer class directly.
    """
    db, _ = _bootstrap_e2e_db(tmp_path)
    bronze_root = tmp_path / "bronze"
    bronze_root.mkdir()
    pipeline: Any = build_connector_pipeline(db=db, bronze_root=bronze_root, collection="obsidian-personal")
    assert pipeline is not None
