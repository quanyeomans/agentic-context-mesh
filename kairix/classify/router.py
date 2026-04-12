"""
Classification router — maps (agent, type) → absolute vault path.

Agent scoping: only valid agents are builder, shape, growth, consultant.
"shared" maps to shared knowledge area.

Path mappings:
  episodic           → <workspace-root>/<agent>/memory/<date>.md
  procedural-rule    → <vault-root>/04-Agent-Knowledge/<agent>/rules.md
  procedural-pattern → <vault-root>/04-Agent-Knowledge/<agent>/patterns.md
  semantic-decision  → <vault-root>/04-Agent-Knowledge/<agent>/decisions.md
  semantic-fact      → <vault-root>/04-Agent-Knowledge/<agent>/facts.md
  entity             → <vault-root>/04-Agent-Knowledge/entities/<type>/<slug>.md
"""

from __future__ import annotations

import os as _os
from datetime import date as _date

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_AGENTS = frozenset({"builder", "shape", "growth", "consultant"})
SHARED_AGENT = "shared"

_VAULT_ROOT = _os.environ.get("KAIRIX_VAULT_ROOT", "/data/obsidian-vault")
_WORKSPACE_ROOT = _os.environ.get("KAIRIX_WORKSPACE_ROOT", "/data/workspaces")
_KNOWLEDGE_ROOT = f"{_VAULT_ROOT}/04-Agent-Knowledge"

# Agents where "shared" maps to the shared knowledge area
_SHARED_AGENT_ROOT = f"{_KNOWLEDGE_ROOT}/shared"


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
    Return the absolute vault path for an agent + classification type.

    Args:
        agent:               Agent name. Must be in VALID_AGENTS or "shared".
        classification_type: One of episodic, procedural-rule, procedural-pattern,
                             semantic-decision, semantic-fact, entity.
        date:                Date string (YYYY-MM-DD) for episodic. Defaults to today.
        entity_type:         Entity type subfolder for entity classification (e.g. "person").
        entity_slug:         Slug for entity file (e.g. "alice-chen").

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

    if classification_type == "episodic":
        date_str = date or _date.today().isoformat()
        if scoped_agent == "shared":
            # shared doesn't have episodic workspace — use builder as fallback
            return f"{_WORKSPACE_ROOT}/builder/memory/{date_str}.md"
        return f"{_WORKSPACE_ROOT}/{scoped_agent}/memory/{date_str}.md"

    if classification_type == "procedural-rule":
        if scoped_agent == "shared":
            return f"{_SHARED_AGENT_ROOT}/rules.md"
        return f"{_KNOWLEDGE_ROOT}/{scoped_agent}/rules.md"

    if classification_type == "procedural-pattern":
        if scoped_agent == "shared":
            return f"{_SHARED_AGENT_ROOT}/patterns.md"
        return f"{_KNOWLEDGE_ROOT}/{scoped_agent}/patterns.md"

    if classification_type == "semantic-decision":
        if scoped_agent == "shared":
            return f"{_SHARED_AGENT_ROOT}/decisions.md"
        return f"{_KNOWLEDGE_ROOT}/{scoped_agent}/decisions.md"

    if classification_type == "semantic-fact":
        if scoped_agent == "shared":
            return f"{_SHARED_AGENT_ROOT}/facts.md"
        return f"{_KNOWLEDGE_ROOT}/{scoped_agent}/facts.md"

    if classification_type == "entity":
        etype = entity_type or "unknown"
        slug = entity_slug or "unknown"
        return f"{_KNOWLEDGE_ROOT}/entities/{etype}/{slug}.md"

    raise ValueError(
        f"Unknown classification type {classification_type!r}. "
        f"Must be one of: episodic, procedural-rule, procedural-pattern, "
        f"semantic-decision, semantic-fact, entity."
    )
