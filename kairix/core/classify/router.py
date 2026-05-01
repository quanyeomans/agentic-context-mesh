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


# Mapping from classification type → filename within the agent knowledge directory
_TYPE_TO_FILENAME: dict[str, str] = {
    "procedural-rule": "rules.md",
    "procedural-pattern": "patterns.md",
    "semantic-decision": "decisions.md",
    "semantic-fact": "facts.md",
}


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
    if agent != SHARED_AGENT and agent not in VALID_AGENTS:
        raise ValueError(f"Invalid agent {agent!r}. Must be one of: {sorted(VALID_AGENTS)} or 'shared'.")

    scoped_agent = "shared" if agent == SHARED_AGENT else agent

    doc_root = str(document_root())
    ws_root = str(workspace_root())
    knowledge_root = f"{doc_root}/04-Agent-Knowledge"

    if classification_type == "episodic":
        date_str = date or _date.today().isoformat()
        effective_agent = "builder" if scoped_agent == "shared" else scoped_agent
        return f"{ws_root}/{effective_agent}/memory/{date_str}.md"

    if classification_type == "entity":
        etype = entity_type or "unknown"
        slug = entity_slug or "unknown"
        return f"{knowledge_root}/entities/{etype}/{slug}.md"

    filename = _TYPE_TO_FILENAME.get(classification_type)
    if filename is not None:
        return f"{knowledge_root}/{scoped_agent}/{filename}"

    raise ValueError(
        f"Unknown classification type {classification_type!r}. "
        f"Must be one of: episodic, procedural-rule, procedural-pattern, "
        f"semantic-decision, semantic-fact, entity."
    )
