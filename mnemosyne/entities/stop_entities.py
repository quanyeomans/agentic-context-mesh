"""
Mnemosyne entity stop list — words that must never be extracted as entities.

This is a managed file. To add entries:
1. Add to the appropriate set below
2. Run `mnemosyne entity purge-noise` to clean existing DB rows
3. Update the changelog in 04-Agent-Knowledge/shared/mnemosyne-ontology.md

See ontology doc for full rationale and rules:
  /data/obsidian-vault/04-Agent-Knowledge/shared/mnemosyne-ontology.md
"""

from __future__ import annotations

# Generic business nouns — too broad to be a searchable entity
BUSINESS_NOUNS: frozenset[str] = frozenset(
    {
        "Business",
        "Market",
        "Markets",
        "Strategy",
        "Data",
        "Platform",
        "Process",
        "Architecture",
        "Engineering",
        "Security",
        "Digital",
        "Industry",
        "Industries",
        "Growth",
        "Consulting",
        "Content",
        "Insights",
        "Approach",
        "Requirements",
        "Work",
        "Workforce",
        "Workplace",
        "Operations",
        "Operating Model",
        "Partnerships",
        "Partnership",
        "Solutions",
        "Frontier Solutions",
        "Capabilities",
        "Capability",
        "Outcomes",
        "Outcome",
        "Delivery",
        "Transformation",
        "Innovation",
        "Performance",
        "Excellence",
        "Leadership",
        "Management",
        "Governance",
        "Compliance",
        "Service",
        "Services",
        "Product",
        "Products",
        "Offering",
        "Offerings",
        "Team",
        "Teams",
        "Organisation",
        "Organization",
        "Customer",
        "Customers",
        "Client",
        "Clients",
        "Project",
        "Projects",
        "Program",
        "Programs",
        "Initiative",
        "Initiatives",
        "Framework",
        "Frameworks",
        "Model",
        "Models",
        "Enterprise",
        "Corporate",
        "Commercial",
        "Public",
        "Private",
    }
)

# Document / writing artefacts — section headings that become false extractions
DOCUMENT_ARTEFACTS: frozenset[str] = frozenset(
    {
        "PLAN",
        "ANALYSIS",
        "PROJECT",
        "REPORT",
        "REVIEW",
        "SUMMARY",
        "DRAFT",
        "NOTES",
        "AGENDA",
        "ACTION",
        "ACTIONS",
        "DECISION",
        "DECISIONS",
        "OVERVIEW",
        "BACKGROUND",
        "CONTEXT",
        "SCOPE",
        "NEXT",
        "STATUS",
        "Introduction",
        "Overview",
        "Background",
        "Summary",
        "Conclusion",
        "Appendix",
        "Annex",
        "Section",
        "Chapter",
        "Part",
        "Research",
        "Analysis",
        "Reports",
        "Report",
        "Review",
    }
)

# Generic tech / AI terms — too common and broad in a tech vault
TECH_TERMS: frozenset[str] = frozenset(
    {
        "AI",
        "ML",
        "LLM",
        "API",
        "Cloud",
        "Infrastructure",
        "Pipeline",
        "Workflow",
        "Automation",
        "Integration",
        "Agent",
        "Agents",
        "Assistant",
        "Assistants",
        "Knowledge",
        "Memory",
        "Context",
        "Retrieval",
        "Patterns",
        "Pattern",
        "Rules",
        "Rule",
        "Reference",
        "Relationships",
        "Templates",
        "Examples",
        "Functions",
        "Scripts",
        "Schemas",
        "Playbooks",
    }
)

# Geographic terms — too broad to boost search
GEOGRAPHIC: frozenset[str] = frozenset(
    {
        "Australia",
        "Asia",
        "Global",
        "International",
        "National",
        "Local",
        "APAC",
        "Americas",
        "Europe",
    }
)

# Common lowercase false positives (shouldn't reach extraction but guard anyway)
LOWERCASE_NOISE: frozenset[str] = frozenset(
    {
        "decision",
        "agents",
        "knowledge",
        "patterns",
        "reports",
        "source",
        "shared",
        "opportunities",
        "person",
        "people",
        "concept",
        "organisation",
        "notes",
        "draft",
        "outline",
        "applied",
        "content",
        "growth",
        "plan",
        "skills",
        "rules",
        "facts",
        "examples",
        "stories",
        "scripts",
    }
)

# Combined stop set — use this for all checks
STOP_ENTITIES: frozenset[str] = BUSINESS_NOUNS | DOCUMENT_ARTEFACTS | TECH_TERMS | GEOGRAPHIC | LOWERCASE_NOISE


def is_stop_entity(name: str) -> bool:
    """Return True if name should never be extracted as an entity.

    Checks:
    - Exact membership in STOP_ENTITIES
    - All-uppercase short strings (document section headers like PLAN, BRIEF)
    - All-lowercase strings (common nouns that slipped past capitalisation filter)
    """
    if name in STOP_ENTITIES:
        return True
    # All-caps words > 2 chars are usually document artefacts (PLAN, BRIEF, etc.)
    if name.isupper() and len(name) > 2:
        return True
    # All-lowercase = not a proper noun
    if name.islower():
        return True
    return False
