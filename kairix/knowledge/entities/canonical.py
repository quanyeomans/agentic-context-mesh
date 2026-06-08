"""Platform-canon entity seeding (Issue #431, EPIC #438).

The operator declares which entities are platform-canon in
``kairix.config.yaml``; the seed function writes them to Neo4j with
operator-supplied summaries + a ``kairix_canonical=true`` marker so the
discovery / suggest / facts_about paths can distinguish them from
discovered entities.

Background:
  The 2026-06-07 post-v2026.6.7 agent test found that the most-referenced
  platform entities (agent codenames like Shape, platform components like
  OpenClaw / Kairix) didn't exist in Neo4j or existed as minimal stubs
  with no useful content. Entity discovery (``kairix.knowledge.entities.seed``)
  scans documents for mentions but didn't pick these up, and even when
  it did, Wikidata enrichment had no match for internal entity names.

  A canonical-entity registry is more reliable: the operator declares
  what's canonical; the seeder writes it; the downstream APIs trust the
  marker.

MVP scope (this module):
  - CanonicalEntity dataclass
  - parse_canonical_entities() — reads the YAML config block
  - seed_canonical_entities() — upserts via the Neo4j client's
    upsert_node interface (same surface as seed.seed_graph for
    discovered entities)

Deferred to follow-up slices:
  - Worker startup integration (call seed_canonical_entities at boot)
  - entity_suggest exclusion (canonicals MUST NOT be flagged as 'new')
  - facts_about canonical-first surfacing
  - BDD scenarios from the issue's acceptance criteria
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CanonicalEntity:
    """Operator-declared platform-canon entity (Issue #431).

    Identity is the ``name`` field (case-sensitive — matches the
    operator's declaration). Aliases let alternative spellings or
    short forms route to the same canonical node (e.g. ``Shape`` with
    alias ``shape-agent``).

    ``entity_type`` is free-form per the operator's preference; common
    values are ``agent``, ``platform_component``, ``person``,
    ``organisation``. The seed function passes it through to
    ``client.upsert_node(label=entity_type, ...)``.
    """

    name: str
    entity_type: str
    summary: str
    aliases: tuple[str, ...] = ()


def parse_canonical_entities(raw: object) -> list[CanonicalEntity]:
    """Parse a ``canonical_entities:`` YAML block into a typed list.

    Accepts ``None`` / missing block / empty list and returns ``[]``
    (operator hasn't declared any canonicals — preserves pre-#431
    behaviour). Skips malformed entries with a WARNING rather than
    raising — a typo in one entry shouldn't break startup.

    Expected YAML shape (per the issue body):

    .. code-block:: yaml

        canonical_entities:
          - name: Shape
            type: agent
            summary: "Strategic + design-orchestration agent..."
            aliases: [shape-agent]
          - name: OpenClaw
            type: platform_component
            summary: "Claude-Code-based agent gateway..."

    Returns the entities in the operator-declared order (callers that
    care about deterministic seed ordering can rely on the input order).
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        logger.warning("parse_canonical_entities: top-level value is not a list (got %s); ignoring", type(raw).__name__)
        return []

    parsed: list[CanonicalEntity] = []
    for index, item in enumerate(raw):
        entity = _parse_one(item, index)
        if entity is not None:
            parsed.append(entity)
    return parsed


def _parse_one(item: object, index: int) -> CanonicalEntity | None:
    """Parse a single YAML entry into a CanonicalEntity, or None on error."""
    if not isinstance(item, dict):
        logger.warning("parse_canonical_entities[%d]: not a dict (got %s); skipping", index, type(item).__name__)
        return None
    name = item.get("name")
    entity_type = item.get("type")
    summary = item.get("summary", "")
    aliases_raw = item.get("aliases", [])
    if not name or not entity_type:
        logger.warning(
            "parse_canonical_entities[%d]: missing required 'name' or 'type' field; skipping (got %r)",
            index,
            item,
        )
        return None
    aliases: tuple[str, ...]
    if isinstance(aliases_raw, list):
        aliases = tuple(str(a) for a in aliases_raw)
    else:
        aliases = ()
        logger.warning(
            "parse_canonical_entities[%d] (%r): 'aliases' is not a list; using empty list",
            index,
            name,
        )
    return CanonicalEntity(
        name=str(name),
        entity_type=str(entity_type),
        summary=str(summary),
        aliases=aliases,
    )


def seed_canonical_entities(client: Any, canonicals: list[CanonicalEntity]) -> int:
    """Upsert canonical entities into Neo4j; returns the count upserted.

    Idempotent: re-running with the same input replays the upserts; the
    Neo4j MERGE semantics in ``client.upsert_node`` ensure no duplicate
    nodes are created. Returns 0 when the Neo4j client is unavailable
    (deployment in degraded mode); operator can re-run after Neo4j
    recovers.

    Each entity gets these properties on the node:
      - ``name`` — operator-declared name (also the MERGE key downstream)
      - ``summary`` — operator-supplied description for entity-card +
        facts_about surfacing (Issue #429 will index this for relevance)
      - ``kairix_canonical`` — boolean marker; downstream queries filter
        on this to distinguish operator-declared canonicals from
        discovery-seeded entities
      - ``aliases`` — list of alternative spellings (when supplied)

    The ``entity_type`` is passed as the node label; production clients
    use it directly in the underlying MERGE cypher.

    Per the issue's acceptance criteria #2 (\"Worker seeds canonical
    entities into Neo4j on warmup (idempotent)\"), this function is the
    target of a worker-startup hook in a follow-up slice.
    """
    if not getattr(client, "available", False):
        logger.warning("seed_canonical_entities: Neo4j not available — skipping (0 seeded)")
        return 0

    upserted = 0
    for entity in canonicals:
        props = _build_props(entity)
        try:
            ok = client.upsert_node(entity.entity_type, _slug_for(entity.name), props)
        except Exception as exc:
            logger.warning(
                "seed_canonical_entities: upsert failed for %r — %s",
                entity.name,
                exc,
            )
            continue
        if ok:
            upserted += 1
        else:
            logger.warning("seed_canonical_entities: upsert returned falsy for %r", entity.name)
    logger.info("seed_canonical_entities: upserted %d of %d declared canonicals", upserted, len(canonicals))
    return upserted


def _slug_for(name: str) -> str:
    """Deterministic slug for the canonical entity's node id.

    Lowercase + underscore-separated. Matches the convention used by
    :func:`kairix.knowledge.entities.seed.seed_graph` for discovered
    entities so the two paths produce comparable ids.
    """
    return name.strip().lower().replace(" ", "_").replace("-", "_")


def _build_props(entity: CanonicalEntity) -> dict[str, Any]:
    """Build the Neo4j property dict for ``entity``."""
    props: dict[str, Any] = {
        "name": entity.name,
        "summary": entity.summary,
        "kairix_canonical": True,
    }
    if entity.aliases:
        # Neo4j supports list-typed properties; store as-is so a
        # downstream alias-resolution query can do MATCH (n) WHERE
        # <alias> IN n.aliases.
        props["aliases"] = list(entity.aliases)
    return props
