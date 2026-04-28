"""Emit entity and relationship stubs as JSON for Neo4j loading.

Writes ``entities/nodes.json`` and ``entities/edges.json`` in a format
compatible with Neo4j's ``apoc.load.json`` or a custom Cypher loader.
"""

from __future__ import annotations

import json
from pathlib import Path

from kairix.knowledge.reflib.extract import RawRelationship
from kairix.knowledge.reflib.resolve import ResolvedEntity, _to_slug


def emit_entity_stubs(
    entities: list[ResolvedEntity],
    relationships: list[RawRelationship],
    output_dir: Path,
) -> tuple[Path, Path]:
    """Write entity nodes and relationship edges to JSON files.

    Args:
        entities: Resolved entity list.
        relationships: Raw relationship list (will be mapped to entity ids).
        output_dir: Root output directory.  Files are written to
            ``output_dir/entities/nodes.json`` and
            ``output_dir/entities/edges.json``.

    Returns:
        Tuple of (nodes_path, edges_path).
    """
    entities_dir = output_dir / "entities"
    entities_dir.mkdir(parents=True, exist_ok=True)

    # Build nodes
    nodes = []
    for e in entities:
        nodes.append(
            {
                "id": e.id,
                "label": e.entity_type,
                "name": e.canonical_name,
                "description": e.description,
                "domains": e.domains,
                "source_docs": e.source_docs[:20],  # cap for readability
                "aliases": e.aliases,
            }
        )

    # Build edge lookup: name → id for resolved entities
    name_to_id: dict[str, str] = {}
    for e in entities:
        name_to_id[e.canonical_name] = e.id
        for alias in e.aliases:
            if alias not in name_to_id:
                name_to_id[alias] = e.id

    # Build edges — deduplicate by (from_id, to_id, kind)
    seen_edges: set[tuple[str, str, str]] = set()
    edges = []
    for r in relationships:
        from_id = name_to_id.get(r.from_name, _to_slug(r.from_name))
        to_id = name_to_id.get(r.to_name, _to_slug(r.to_name))
        if not from_id or not to_id:
            continue
        edge_key = (from_id, to_id, r.kind)
        if edge_key in seen_edges:
            continue
        seen_edges.add(edge_key)
        edges.append(
            {
                "from_id": from_id,
                "to_id": to_id,
                "kind": r.kind,
                "source_doc": r.source_doc,
            }
        )

    nodes_path = entities_dir / "nodes.json"
    edges_path = entities_dir / "edges.json"

    nodes_path.write_text(
        json.dumps(nodes, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    edges_path.write_text(
        json.dumps(edges, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return nodes_path, edges_path
