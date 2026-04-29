"""
Classification router — maps (agent, type) → absolute document path.

Agent scoping: only valid agents are builder, shape, growth, consultant.
"shared" maps to shared knowledge area.

Path mappings:
  episodic           → <workspace-root>/<agent>/memory/<date>.md
  procedural-rule    → <document-root>/04-Agent-Knowledge/<agent>/rules.md
  procedural-pattern → <document-root>/04-Agent-Knowledge/<agent>/patterns.md
  semantic-decision  → <document-root>/04-Agent-Knowledge/<agent>/decisions.md
  semantic-fact      → <document-root>/04-Agent-Knowledge/<agent>/facts.md
  entity             → <document-root>/04-Agent-Knowledge/entities/<type>/<slug>.md
"""

from __future__ import annotations

from datetime import date as _date

from kairix.paths import document_root, workspace_root

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_AGENTS = frozenset({"builder", "shape", "growth", "consultant", "shared"})
SHARED_AGENT = "shared"


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


def resolve_target_path(
    agent: str,
    classification_type: str,
    date: str | None = None,
    entity_type: str | None = None,
    entity_slug: str | None = None,
) -> str:
    """
    Return the absolute document path for an agent + classification type.

    Args:
        agent:               Agent name. Must be in VALID_AGENTS or "shared".
        classification_type: One of episodic, procedural-rule, procedural-pattern,
                             semantic-decision, semantic-fact, entity.
        date:                Date string (YYYY-MM-DD) for episodic. Defaults to today.
        entity_type:         Entity type subfolder for entity classification (e.g. "person").
        entity_slug:         Slug for entity file (e.g. "jordan-blake").

    Returns:
        Absolute path string.

    Raises:
        ValueError: If agent is invalid or classification_type is unknown.
    """
    # Resolve agent name
    if agent == SHARED_AGENT:
        scoped_agent = "shared"
    elif agent in VALID_AGENTS:
        scoped_agent = agent
    else:
        raise ValueError(f"Invalid agent {agent!r}. Must be one of: {sorted(VALID_AGENTS)} or 'shared'.")

    # Lazy resolution — env vars may be set after import time
    doc_root = str(document_root())
    ws_root = str(workspace_root())
    knowledge_root = f"{doc_root}/04-Agent-Knowledge"
    shared_agent_root = f"{knowledge_root}/shared"

    if classification_type == "episodic":
        date_str = date or _date.today().isoformat()
        if scoped_agent == "shared":
            # shared doesn't have episodic workspace — use builder as fallback
            return f"{ws_root}/builder/memory/{date_str}.md"
        return f"{ws_root}/{scoped_agent}/memory/{date_str}.md"

    if classification_type == "procedural-rule":
        if scoped_agent == "shared":
            return f"{shared_agent_root}/rules.md"
        return f"{knowledge_root}/{scoped_agent}/rules.md"

    if classification_type == "procedural-pattern":
        if scoped_agent == "shared":
            return f"{shared_agent_root}/patterns.md"
        return f"{knowledge_root}/{scoped_agent}/patterns.md"

    if classification_type == "semantic-decision":
        if scoped_agent == "shared":
            return f"{shared_agent_root}/decisions.md"
        return f"{knowledge_root}/{scoped_agent}/decisions.md"

    if classification_type == "semantic-fact":
        if scoped_agent == "shared":
            return f"{shared_agent_root}/facts.md"
        return f"{knowledge_root}/{scoped_agent}/facts.md"

    if classification_type == "entity":
        etype = entity_type or "unknown"
        slug = entity_slug or "unknown"
        return f"{knowledge_root}/entities/{etype}/{slug}.md"

    raise ValueError(
        f"Unknown classification type {classification_type!r}. "
        f"Must be one of: episodic, procedural-rule, procedural-pattern, "
        f"semantic-decision, semantic-fact, entity."
    )
