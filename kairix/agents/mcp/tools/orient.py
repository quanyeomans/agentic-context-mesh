"""MCP tool adapters — orient domain (``usage_guide`` / ``bootstrap`` /
``recommend_capabilities`` / ``entity_suggest`` / ``entity_validate``).

The Orient loop-group surface: the tools an agent calls to learn the kairix
surface and orient itself at session start. Each ``tool_<name>`` body is a thin
adapter around the matching ``kairix.use_cases`` use case + its
``<name>_output_to_envelope`` serialiser. :data:`BINDINGS` publishes the
registered tools so ``server.py`` registers this surface by walking
``CAPABILITIES_CATALOG``. Behaviour is byte-identical to the pre-split server.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from kairix.agents.mcp.cold_start import require_ready
from kairix.agents.mcp.tools._common import RegistrationContext, ToolBinding

logger = logging.getLogger(__name__)

__all__ = [
    "BINDINGS",
    "tool_bootstrap",
    "tool_entity_suggest",
    "tool_entity_validate",
    "tool_recommend_capabilities",
    "tool_usage_guide",
]


# ---------------------------------------------------------------------------
# Tool bodies — pure Python, no mcp dependency.
# ---------------------------------------------------------------------------


def tool_usage_guide(
    topic: str = "",
    *,
    guide_path: Path | None = None,
    deps: Any = None,
) -> dict[str, Any]:
    """
    Return the kairix agent usage guide, or a section of it filtered by topic.

    Thin adapter around ``kairix.use_cases.usage_guide.run_usage_guide``.
    Use this tool when you are unsure how to use kairix, when a search
    returns unexpected results, or when you want to understand a feature.

    The optional ``deps`` parameter forwards a ``UsageGuideDeps`` directly
    to the use case — production callers leave it None. The legacy
    ``guide_path`` parameter is preserved as the operator-facing override.
    """
    from kairix.use_cases.usage_guide import run_usage_guide, usage_guide_output_to_envelope

    out = run_usage_guide(topic, guide_path=guide_path, deps=deps)
    return usage_guide_output_to_envelope(out)


def tool_recommend_capabilities(
    task: str,
    *,
    agent: str | None = None,
    deps: Any = None,
    flag_reader: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Recommend which kairix tool or local skill fits a described task.

    Thin adapter around ``kairix.use_cases.recommend.run_recommend``. Use
    when you are unsure which kairix tool, skill, slash-command, sub-agent,
    or workflow fits a task — describe the task and get a ranked list of
    capabilities, each with why-it-fits and a ready-to-call invocation.

    Named for its registered MCP tool ``recommend_capabilities`` — the single
    wire name lives in the catalogue row's ``mcp_tool`` and the registration
    looks the binding up by it, so this adapter carries no short-form alias.
    The F30 outcome-test convention + the capability-affordance gate key on the
    ``tool_<registered-tool-name>`` shape, which this name satisfies directly.

    Flag-gated at THIS adapter level (not inside ``run_recommend``): when
    the ``recommender`` flag is OFF, returns a disabled envelope WITHOUT
    calling ``run_recommend``. ``deps`` forwards a ``RecommendDeps`` to the
    use case; ``flag_reader`` is the flag DI seam (default reads
    ``flag("recommender")``). Production callers leave both None.
    """
    from kairix.use_cases.recommend import (
        default_recommender_flag_reader,
        recommend_output_to_envelope,
        recommender_disabled_output,
        run_recommend,
    )

    read_flag = flag_reader if flag_reader is not None else default_recommender_flag_reader
    if not read_flag():
        return recommend_output_to_envelope(recommender_disabled_output(task))
    out = run_recommend(task, agent=agent, deps=deps)
    return recommend_output_to_envelope(out)


def tool_bootstrap(
    agent: str,
    max_memory_days: int = 3,
    *,
    deps: Any = None,
) -> dict[str, Any]:
    """Return the agent orientation envelope (#246 W1).

    Thin adapter around ``kairix.use_cases.bootstrap.run_bootstrap``.
    Returns the agent's role, current ``Board.md``, recent memory
    entries, active goals, and a structured health snapshot — the
    single call an agent makes at session start (or topic switch) to
    absorb its current state. Never raises; degradation is surfaced via
    the ``health`` field with a prescriptive ``next_action``.

    The optional ``deps`` parameter forwards a ``BootstrapDeps`` directly
    to the use case — production callers leave it None.
    """
    from kairix.use_cases.bootstrap import bootstrap_output_to_envelope, run_bootstrap

    out = run_bootstrap(agent, deps=deps, max_memory_days=max_memory_days)
    return bootstrap_output_to_envelope(out)


def tool_entity_suggest(
    text: str,
    *,
    deps: Any = None,
) -> dict[str, Any]:
    """Suggest entities found in arbitrary text by running NER + Neo4j cross-ref.

    Thin adapter around ``kairix.use_cases.entity.run_entity_suggest``.
    Use to spot people, organisations, places mentioned in prose so an
    operator (or another agent) can decide whether to add them to the
    knowledge graph.
    """
    from kairix.use_cases.entity import entity_suggest_output_to_envelope, run_entity_suggest

    out = run_entity_suggest(text, deps=deps)
    return entity_suggest_output_to_envelope(out)


def tool_entity_validate(
    name: str,
    update: bool = False,
    *,
    deps: Any = None,
) -> dict[str, Any]:
    """Validate an entity against Wikidata and optionally update Neo4j.

    Thin adapter around ``kairix.use_cases.entity.run_entity_validate``.
    Use to confirm a graph entity has a real-world match (qid) and
    optionally write that qid back to the Neo4j node.
    """
    from kairix.use_cases.entity import entity_validate_output_to_envelope, run_entity_validate

    out = run_entity_validate(name, update=update, deps=deps)
    return entity_validate_output_to_envelope(out)


# ---------------------------------------------------------------------------
# Registration bindings — one per registered MCP tool in this domain.
# ---------------------------------------------------------------------------

_BOOTSTRAP_DESCRIPTION = (
    "Call at session start or whenever you switch topics. "
    "Returns your agent role, current board, recent memory, and active goals — "
    "orients you in the team's current state. "
    "If health.vector_search != 'ok', surface that to your human. "
    "If the result has error_code=KAIRIX_COLD_START, wait retry_after_ms and retry; "
    "do not begin the task context-blind. "
    "Expected p99: 5s warm, 10s cold. Recommended client timeout: 30s."
)

_RECOMMEND_DESCRIPTION = (
    "Call when you are unsure which kairix tool, skill, slash-command, "
    "sub-agent, or workflow fits a task. Describe the task; get a ranked "
    "list of capabilities, each with why-it-fits and a ready-to-call "
    "invocation. Read-only — no LLM between you and your tools. "
    "Gated by the 'recommender' feature flag (returns a disabled "
    "envelope when OFF). Expected p99: 1s warm. Recommended client timeout: 10s."
)


def _make_usage_guide(_ctx: RegistrationContext) -> Callable[..., Any]:
    def usage_guide(topic: str = "") -> dict[str, Any]:
        """Return the kairix agent usage guide. Call this when unsure how to use kairix.

        Expected p99: 1s warm, 2s cold. Recommended client timeout: 10s.
        """
        return tool_usage_guide(topic=topic)

    return usage_guide


def _make_bootstrap(ctx: RegistrationContext) -> Callable[..., Any]:
    def bootstrap(agent: str, max_memory_days: int = 3) -> dict[str, Any]:
        """Return the agent orientation envelope: role, board, recent memory, goals, health."""
        if cold := require_ready("bootstrap", ctx.readiness_check):
            return cold
        return tool_bootstrap(agent=agent, max_memory_days=max_memory_days)

    return bootstrap


def _make_recommend_capabilities(_ctx: RegistrationContext) -> Callable[..., Any]:
    def recommend_capabilities(task: str, agent: str = "") -> dict[str, Any]:
        """Rank kairix tools + local skills for a task. Read-only."""
        # ``agent`` (default "") is forwarded as-is; ``run_recommend`` accepts
        # ``str | None`` and only logs it (v1 does not personalise ranking),
        # so the empty-string default is harmless — no ``or None`` coercion.
        return tool_recommend_capabilities(task=task, agent=agent)

    return recommend_capabilities


def _make_entity_suggest(_ctx: RegistrationContext) -> Callable[..., Any]:
    def entity_suggest(text: str) -> dict[str, Any]:
        """Suggest entities (people, organisations, places) found in text via NER + Neo4j cross-ref."""
        return tool_entity_suggest(text=text)

    return entity_suggest


def _make_entity_validate(_ctx: RegistrationContext) -> Callable[..., Any]:
    def entity_validate(name: str, update: bool = False) -> dict[str, Any]:
        """Validate a named entity against Wikidata and optionally write the qid to Neo4j."""
        return tool_entity_validate(name=name, update=update)

    return entity_validate


BINDINGS: tuple[ToolBinding, ...] = (
    # ``usage_guide`` / ``entity_suggest`` / ``entity_validate`` register with
    # ``description=None`` so FastMCP uses the body closure's docstring, exactly
    # as the pre-split ``@server.tool()`` (no-description) registrations did.
    ToolBinding(name="usage_guide", description=None, make=_make_usage_guide, warm_gated=False),
    ToolBinding(name="bootstrap", description=_BOOTSTRAP_DESCRIPTION, make=_make_bootstrap, warm_gated=True),
    ToolBinding(
        name="recommend_capabilities",
        description=_RECOMMEND_DESCRIPTION,
        make=_make_recommend_capabilities,
        warm_gated=False,
    ),
    ToolBinding(name="entity_suggest", description=None, make=_make_entity_suggest, warm_gated=True),
    ToolBinding(name="entity_validate", description=None, make=_make_entity_validate, warm_gated=True),
)
