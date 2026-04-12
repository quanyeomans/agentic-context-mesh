"""
kairix.graph.models — Node and edge dataclasses for the Kairix graph layer.

Node types mirror the vault structure defined in ADR-014:
  OrganisationNode — from 02-Areas/00-Clients/{Org}/ index files
  PersonNode       — from Network/People-Notes/
  OutcomeNode      — from 05-Knowledge/01-Domain-Outcomes/

Edge kinds represent relationships extracted from frontmatter and wikilinks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class EdgeKind(str, Enum):
    WORKS_AT = "WORKS_AT"
    KNOWS = "KNOWS"
    MENTIONS = "MENTIONS"
    RELATED_TO = "RELATED_TO"
    CLIENT_OF = "CLIENT_OF"


@dataclass
class OrganisationNode:
    """
    Represents an organisation entity.

    id: slug derived from vault directory name (e.g. 'bupa', 'avanade')
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
    aliases: list[str] = field(default_factory=list)

    def to_neo4j_props(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "tier": self.tier,
            "engagement_status": self.engagement_status,
            "vault_path": self.vault_path,
            "industry": self.industry,
            "geography": self.geography,
            "stakeholder_personas": self.stakeholder_personas,
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
    relationship_type: 'client-stakeholder' | 'network' | 'avanade-colleague'
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

    def to_neo4j_props(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "org": self.org,
            "role": self.role,
            "relationship_type": self.relationship_type,
            "last_interaction": self.last_interaction,
            "vault_path": self.vault_path,
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

    def to_neo4j_props(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "domain": self.domain,
            "vault_path": self.vault_path,
        }


@dataclass
class GraphEdge:
    """A directed relationship between two nodes."""

    from_id: str
    from_label: str
    to_id: str
    to_label: str
    kind: EdgeKind
    props: dict = field(default_factory=dict)
