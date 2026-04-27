"""
kairix.graph — Neo4j graph layer for entity and relationship management.

Provides:
  client      — Neo4jClient: connection, constraint init, upsert helpers
  models      — Node/edge dataclasses (Organisation, Person, Outcome, edges)
  query       — Cypher query helpers for CONTEXTUAL_PREP and entity lookup

Connection is configured via env vars:
  KAIRIX_NEO4J_URI      — Bolt URI (default: bolt://localhost:7687)
  KAIRIX_NEO4J_USER     — username (default: neo4j)
  KAIRIX_NEO4J_PASSWORD — password (required)

Returns empty results rather than raising when Neo4j is unavailable.
"""

from kairix.graph.client import Neo4jClient, get_client
from kairix.graph.models import (
    ConceptNode,
    EdgeKind,
    FrameworkNode,
    OrganisationNode,
    OutcomeNode,
    PersonNode,
    PublicationNode,
    TechnologyNode,
)

__all__ = [
    "ConceptNode",
    "EdgeKind",
    "FrameworkNode",
    "Neo4jClient",
    "OrganisationNode",
    "OutcomeNode",
    "PersonNode",
    "PublicationNode",
    "TechnologyNode",
    "get_client",
]
