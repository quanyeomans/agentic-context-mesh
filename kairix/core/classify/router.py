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

# Legacy built-in agent names. Deployments that predate the config-driven
# ``agents:`` block rely on these; :func:`valid_agents` unions them with
# the configured names so existing setups keep working (default-safe, #472).
VALID_AGENTS = frozenset({"builder", "shape", "growth", "consultant", "shared"})
SHARED_AGENT = "shared"

# F21 affordance appended to every invalid-agent rejection so the operator
# (or calling agent) knows the exact fix without reading source.
_INVALID_AGENT_ACTION = (
    "fix: add the agent to the agents: block in kairix.config.yaml. next: re-run kairix doctor agent --all."
)


# ---------------------------------------------------------------------------
# Config-driven agent allowlist (#472)
# ---------------------------------------------------------------------------


def _configured_agent_names(config: dict[str, object] | None) -> frozenset[str]:
    """Return the agent names declared in the config ``agents:`` block.

    Accepts both schema generations:

    - mapping shape (``agents: {name: {surfaces: [...]}}``) — emitted by
      ``kairix onboard scan --yaml`` and consumed by
      :func:`kairix.core.agents.scope.load_agent_scopes`;
    - legacy list shape (``agents: [{name: ..., write_path: ...}]``) —
      consumed by :func:`kairix.core.search.registry.parse_agent_registry`.

    A malformed block yields the empty set — ``kairix config validate``
    owns reporting shape errors; the allowlist degrades to legacy names.
    """
    if not config:
        return frozenset()
    agents_raw = config.get("agents")
    if isinstance(agents_raw, dict):
        return frozenset(str(name) for name in agents_raw)
    if isinstance(agents_raw, list):
        return frozenset(str(item["name"]) for item in agents_raw if isinstance(item, dict) and item.get("name"))
    return frozenset()


def valid_agents(config: dict[str, object] | None = None) -> frozenset[str]:
    """Return every acceptable agent name: configured union legacy.

    ``config`` is the injection seam — tests pass a parsed dict (``{}``
    pins the no-config legacy behaviour); production callers leave it
    ``None`` and the loader reads ``kairix.config.yaml``.
    """
    cfg = config if config is not None else _load_top_level_config()
    return VALID_AGENTS | _configured_agent_names(cfg)


def invalid_agent_message(agent: str, allowed: frozenset[str]) -> str:
    """F21-actionable rejection message listing the actually-valid names."""
    return f"Invalid agent {agent!r}. Must be one of: {sorted(allowed)}. {_INVALID_AGENT_ACTION}"


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
        agent:               Agent name. Must be in :func:`valid_agents` (configured
                             ``agents:`` block union the legacy built-in set) or "shared".
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
    allowed = valid_agents(config)
    if agent != SHARED_AGENT and agent not in allowed:
        raise ValueError(invalid_agent_message(agent, allowed))

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
