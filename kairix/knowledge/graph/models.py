"""
kairix.knowledge.graph.models — Node and edge dataclasses for the Kairix graph layer.

Node types mirror the vault structure defined in ADR-014:
  OrganisationNode — from 02-Areas/00-Clients/{Org}/ index files
  PersonNode       — from Network/People-Notes/
  OutcomeNode      — from 05-Knowledge/01-Domain-Outcomes/

Edge kinds represent relationships extracted from frontmatter and wikilinks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# F17 — every node type's to_dict() emits "vault_path" as a key; extract so a
# schema rename hits a single edit site.
_KEY_VAULT_PATH = "vault_path"


class NodeLabel(str, Enum):
    """Valid Neo4j node labels. Used to validate GraphEdge labels and prevent injection.

    The 5 labels added in 2026-05 (Place, Product, Vocation, Industry,
    LegalCompliance) come from the iter_5 entity-modelling enrichment
    (GH #343). They mirror the 9 pipeline classes whose enriched
    entities land in production via the cypher-shell deployment (see
    entity-modelling repo, docs/kairix-deployment-plan.md §4.2).
    """

    Document = "Document"
    Organisation = "Organisation"
    Person = "Person"
    Outcome = "Outcome"
    Concept = "Concept"
    Framework = "Framework"
    Technology = "Technology"
    Publication = "Publication"
    # iter_5 enrichment additions (GH #343, 2026-05-29)
    Place = "Place"
    Product = "Product"
    Vocation = "Vocation"
    Industry = "Industry"
    LegalCompliance = "LegalCompliance"


class EdgeKind(str, Enum):
    """Valid Neo4j relationship types. Used to validate GraphEdge.kind.

    The 10 edge kinds added in 2026-05 (LOCATED_IN_COUNTRY, HEADQUARTERED_IN,
    CITIZEN_OF, OPERATES_IN, HAS_OCCUPATION, DEVELOPED_BY, FIELD_OF, RUNS_ON,
    APPLIES_IN, CO_OCCURS_IN_CORPUS) come from the iter_5 entity-modelling
    enrichment (GH #343). MENTIONED_IN from the pipeline output is
    inverted to use the existing MENTIONS edge in canonical kairix
    direction (Document → Entity).
    """

    WORKS_AT = "WORKS_AT"
    KNOWS = "KNOWS"
    MENTIONS = "MENTIONS"
    RELATED_TO = "RELATED_TO"
    CLIENT_OF = "CLIENT_OF"
    TEACHES = "TEACHES"
    PUBLISHES = "PUBLISHES"
    AUTHORED_BY = "AUTHORED_BY"
    IMPLEMENTS = "IMPLEMENTS"
    PART_OF = "PART_OF"
    DESCRIBED_IN = "DESCRIBED_IN"
    # iter_5 enrichment additions (GH #343, 2026-05-29)
    LOCATED_IN_COUNTRY = "LOCATED_IN_COUNTRY"
    HEADQUARTERED_IN = "HEADQUARTERED_IN"
    CITIZEN_OF = "CITIZEN_OF"
    OPERATES_IN = "OPERATES_IN"
    HAS_OCCUPATION = "HAS_OCCUPATION"
    DEVELOPED_BY = "DEVELOPED_BY"
    FIELD_OF = "FIELD_OF"
    RUNS_ON = "RUNS_ON"
    APPLIES_IN = "APPLIES_IN"
    CO_OCCURS_IN_CORPUS = "CO_OCCURS_IN_CORPUS"


@dataclass
class OrganisationNode:
    """
    Represents an organisation entity.

    id: slug derived from vault directory name (e.g. 'bupa', 'acme-corp')
    name: canonical display name
    industry: list of industry tags (e.g. ['healthcare', 'insurance'])
    geography: list of geography tags (e.g. ['ANZ', 'AU'])
    tier: relationship tier ('client' | 'partner' | 'research-org' | 'market-body')
    stakeholder_personas: list of persona tags for CONTEXTUAL_PREP expansion
    engagement_status: 'active' | 'inactive' | 'prospect'
    vault_path: relative path to canonical note in vault
    aliases: alternative names / abbreviations
    """

    id: str
    name: str
    tier: str = "client"
    engagement_status: str = "active"
    vault_path: str = ""
    industry: list[str] = field(default_factory=list)
    geography: list[str] = field(default_factory=list)
    stakeholder_personas: list[str] = field(default_factory=list)
    focus_areas: list[str] = field(default_factory=list)
    key_platforms: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)

    def to_neo4j_props(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "tier": self.tier,
            "engagement_status": self.engagement_status,
            _KEY_VAULT_PATH: self.vault_path,
            "industry": self.industry,
            "geography": self.geography,
            "stakeholder_personas": self.stakeholder_personas,
            "focus_areas": self.focus_areas,
            "key_platforms": self.key_platforms,
            "aliases": self.aliases,
        }


@dataclass
class PersonNode:
    """
    Represents a person entity.

    id: slug derived from vault note filename (e.g. 'felicity-herron')
    name: canonical display name
    org: organisation id this person belongs to
    role: job title / role description
    interests: list of interest/topic tags for CONTEXTUAL_PREP expansion
    relationship_type: 'client-stakeholder' | 'network' | 'professional-network'
    last_interaction: ISO date string of most recent interaction (YYYY-MM-DD)
    vault_path: relative path to canonical note in vault
    aliases: alternative names
    """

    id: str
    name: str
    org: str = ""
    role: str = ""
    relationship_type: str = "network"
    last_interaction: str = ""
    vault_path: str = ""
    interests: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)

    def to_neo4j_props(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "org": self.org,
            "role": self.role,
            "relationship_type": self.relationship_type,
            "last_interaction": self.last_interaction,
            _KEY_VAULT_PATH: self.vault_path,
            "interests": self.interests,
            "aliases": self.aliases,
        }


@dataclass
class OutcomeNode:
    """
    Represents a domain outcome or knowledge area.

    id: slug (e.g. 'digital-health', 'ai-governance')
    name: canonical display name
    domain: parent domain (e.g. 'healthcare', 'technology')
    vault_path: relative path to canonical outcome note
    """

    id: str
    name: str
    domain: str = ""
    vault_path: str = ""

    def to_neo4j_props(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "domain": self.domain,
            _KEY_VAULT_PATH: self.vault_path,
        }


@dataclass
class ConceptNode:
    """
    Represents a concept entity (e.g. 'zero-trust', 'design-thinking').

    id: slug
    name: canonical display name
    domain: parent domain
    vault_path: relative path to canonical note
    aliases: alternative names
    """

    id: str
    name: str
    domain: str = ""
    vault_path: str = ""
    aliases: list[str] = field(default_factory=list)

    def to_neo4j_props(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "domain": self.domain,
            _KEY_VAULT_PATH: self.vault_path,
            "aliases": self.aliases,
        }


@dataclass
class FrameworkNode:
    """
    Represents a framework entity (e.g. 'togaf', 'safe', 'itil').

    id: slug
    name: canonical display name
    domain: parent domain
    vault_path: relative path to canonical note
    aliases: alternative names
    """

    id: str
    name: str
    domain: str = ""
    vault_path: str = ""
    aliases: list[str] = field(default_factory=list)

    def to_neo4j_props(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "domain": self.domain,
            _KEY_VAULT_PATH: self.vault_path,
            "aliases": self.aliases,
        }


@dataclass
class TechnologyNode:
    """
    Represents a technology entity (e.g. 'neo4j', 'kubernetes', 'azure-openai').

    id: slug
    name: canonical display name
    category: technology category (e.g. 'database', 'cloud', 'ai-ml')
    vault_path: relative path to canonical note
    aliases: alternative names
    """

    id: str
    name: str
    category: str = ""
    vault_path: str = ""
    aliases: list[str] = field(default_factory=list)

    def to_neo4j_props(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            _KEY_VAULT_PATH: self.vault_path,
            "aliases": self.aliases,
        }


@dataclass
class PublicationNode:
    """
    Represents a publication entity (e.g. a book, whitepaper, standard).

    id: slug
    name: canonical display name
    authors: list of author names
    year: publication year
    vault_path: relative path to canonical note
    aliases: alternative names
    """

    id: str
    name: str
    authors: list[str] = field(default_factory=list)
    year: str = ""
    vault_path: str = ""
    aliases: list[str] = field(default_factory=list)

    def to_neo4j_props(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "authors": self.authors,
            "year": self.year,
            _KEY_VAULT_PATH: self.vault_path,
            "aliases": self.aliases,
        }


_VALID_LABELS: frozenset[str] = frozenset(label.value for label in NodeLabel)


@dataclass
class GraphEdge:
    """A directed relationship between two nodes."""

    from_id: str
    from_label: str
    to_id: str
    to_label: str
    kind: EdgeKind
    props: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.from_label not in _VALID_LABELS:
            raise ValueError(
                f"from_label {self.from_label!r} is not a valid node label. Valid labels: {sorted(_VALID_LABELS)}"
            )
        if self.to_label not in _VALID_LABELS:
            raise ValueError(
                f"to_label {self.to_label!r} is not a valid node label. Valid labels: {sorted(_VALID_LABELS)}"
            )
