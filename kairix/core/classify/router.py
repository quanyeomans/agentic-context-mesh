"""
Classification router — maps (agent, type) → absolute document path.

Agent scoping: only valid agents are builder, shape, growth, consultant.
"shared" maps to shared knowledge area.

Path mappings:
  episodic           → <AgentScope.writable_path()>/<date>.md  (PR 1.2 / #420)
  procedural-rule    → <document-root>/04-Agent-Knowledge/<agent>/rules.md
  procedural-pattern → <document-root>/04-Agent-Knowledge/<agent>/patterns.md
  semantic-decision  → <document-root>/04-Agent-Knowledge/<agent>/decisions.md
  semantic-fact      → <document-root>/04-Agent-Knowledge/<agent>/facts.md
  entity             → <document-root>/04-Agent-Knowledge/entities/<type>/<slug>.md

PR 1.2 / #420 — the episodic write path now follows
:meth:`kairix.core.agents.scope.AgentScope.writable_path` (the surface
labelled ``"memory"``) instead of the legacy
``<workspace-root>/<agent>/memory/<date>.md`` formula. The remaining
classification types still flow through the document-root convention;
they will move to AgentScope in a later PR once each carries an
explicit operator-facing surface (rules / decisions / etc.).
"""

from __future__ import annotations

from datetime import date as _date

from kairix.paths import document_root

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


def _resolve_episodic_write_dir(agent: str, config: dict[str, object] | None) -> str:
    """Return the directory under which episodic memory files should land.

    Routes through :func:`kairix.core.agents.scope.get_agent_scope` →
    :meth:`AgentScope.writable_path` (the surface labelled ``"memory"``,
    falling back to the first surface). ``config`` is the test seam;
    production callers leave it None and the loader reads
    ``kairix.config.yaml``.
    """
    from kairix.core.agents.scope import get_agent_scope

    effective = "builder" if agent == SHARED_AGENT else agent
    cfg = config if config is not None else _load_top_level_config()
    scope = get_agent_scope(effective, config=cfg)
    return str(scope.writable_path())


def _load_top_level_config() -> dict[str, object] | None:
    """Read ``kairix.config.yaml`` as a top-level dict, or None on missing.

    Thin alias over :func:`kairix.paths.load_top_level_config`.
    """
    from kairix.paths import load_top_level_config

    return load_top_level_config()


def resolve_target_path(
    agent: str,
    classification_type: str,
    date: str | None = None,
    entity_type: str | None = None,
    entity_slug: str | None = None,
    config: dict[str, object] | None = None,
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
        config:              Optional parsed ``kairix.config.yaml`` dict — test
                             seam for AgentScope-based episodic resolution.

    Returns:
        Absolute path string.

    Raises:
        ValueError: If agent is invalid or classification_type is unknown.
    """
    if agent != SHARED_AGENT and agent not in VALID_AGENTS:
        raise ValueError(f"Invalid agent {agent!r}. Must be one of: {sorted(VALID_AGENTS)} or 'shared'.")

    scoped_agent = "shared" if agent == SHARED_AGENT else agent

    doc_root = str(document_root())
    knowledge_root = f"{doc_root}/04-Agent-Knowledge"

    if classification_type == "episodic":
        date_str = date or _date.today().isoformat()
        write_dir = _resolve_episodic_write_dir(scoped_agent, config)
        return f"{write_dir}/{date_str}.md"

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
