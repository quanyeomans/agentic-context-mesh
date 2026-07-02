"""MCP tool adapters — synthesis domain (``prep`` / ``research`` / ``contradict``
/ ``brief``).

Each ``tool_<name>`` body is a thin adapter around the matching
``kairix.use_cases`` use case + its ``<name>_output_to_envelope`` serialiser.
:data:`BINDINGS` publishes the registered tools so ``server.py`` registers this
surface by walking ``CAPABILITIES_CATALOG``. Behaviour is byte-identical to the
pre-split server.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Literal

from kairix.agents.mcp.cold_start import require_ready
from kairix.agents.mcp.tools._common import DEFAULT_SCOPE, RegistrationContext, ToolBinding
from kairix.core.search.scope import Scope

logger = logging.getLogger(__name__)

__all__ = [
    "BINDINGS",
    "tool_brief",
    "tool_contradict",
    "tool_prep",
    "tool_research",
]


# ---------------------------------------------------------------------------
# Tool bodies — pure Python, no mcp dependency.
# ---------------------------------------------------------------------------


def tool_prep(
    query: str,
    agent: str | None = None,
    tier: Literal["l0", "l1"] = "l0",
    scope: Scope = DEFAULT_SCOPE,
    *,
    deps: Any = None,
) -> dict[str, Any]:
    """Get a short summary of a topic before committing to a full search.

    Thin adapter around ``kairix.use_cases.prep.run_prep``. Choose 'l0'
    for 2-3 sentences or 'l1' for a structured overview. Uses less
    resources than a full search — good for quick context checks.
    Retrieves relevant documents first, then summarises from them.

    The optional ``deps`` parameter forwards a ``PrepDeps`` directly
    to the use case — production callers leave it None.
    """
    from kairix.use_cases.prep import prep_output_to_envelope, run_prep

    out = run_prep(query, agent=agent, scope=scope, tier=tier, deps=deps)
    return prep_output_to_envelope(out)


def tool_research(
    query: str,
    agent: str | None = None,
    max_turns: int = 4,
    *,
    deps: Any = None,
) -> dict[str, Any]:
    """Ask a research question. The system searches multiple times, refining
    its approach until it finds a good answer or reports what's missing.

    Thin adapter around ``kairix.use_cases.research.run_research_use_case``.
    Use this for complex questions that need more than a quick search.
    For simple lookups, use search instead — it's faster.

    The optional ``deps`` parameter forwards a ``ResearchDeps`` directly
    to the use case — production callers leave it None.

    ``agent`` is accepted for signature parity with the other tools and
    logged for traceability; the research use case is agent-agnostic
    today (no per-agent scope/tier filtering), so it isn't threaded
    further.
    """
    from kairix.use_cases.research import research_output_to_envelope, run_research_use_case

    logger.info("mcp.research: agent=%r turns<=%d", agent, max_turns)
    out = run_research_use_case(query, max_turns=max_turns, deps=deps)
    return research_output_to_envelope(out)


def tool_contradict(
    content: str,
    agent: str | None = None,
    top_k: int = 5,
    threshold: float = 0.45,
    top_claims: int = 3,
    scope: Scope = DEFAULT_SCOPE,
    *,
    deps: Any = None,
) -> dict[str, Any]:
    """Check new content against existing knowledge for contradictions.

    Thin adapter around ``kairix.use_cases.contradict.run_contradict``.
    Use before writing new facts — catches conflicts with what's already
    in the knowledge base. Returns a list of contradicting documents with
    scores and explanations.

    The optional ``deps`` parameter forwards a ``ContradictDeps`` directly
    to the use case — production callers leave it None.
    """
    from kairix.use_cases.contradict import contradict_output_to_envelope, run_contradict

    out = run_contradict(
        content,
        agent=agent,
        scope=scope,
        top_k=top_k,
        threshold=threshold,
        top_claims=top_claims,
        deps=deps,
    )
    return contradict_output_to_envelope(out)


def tool_brief(
    agent: str,
    *,
    deps: Any = None,
) -> dict[str, Any]:
    """Generate a session briefing and return its content + path.

    Thin adapter around ``kairix.use_cases.brief.run_brief``. Use before
    starting work — gives an agent the operator's recent decisions,
    notes, and entity stub in one structured payload.

    The optional ``deps`` parameter forwards a ``BriefDeps`` directly to
    the use case — production callers leave it None.
    """
    from kairix.use_cases.brief import brief_output_to_envelope, run_brief

    out = run_brief(agent, deps=deps)
    return brief_output_to_envelope(out)


# ---------------------------------------------------------------------------
# Registration bindings — one per registered MCP tool in this domain.
# ---------------------------------------------------------------------------

_PREP_DESCRIPTION = (
    "Call when you need lightweight context preparation before deeper work. "
    "If the result has error_code=KAIRIX_COLD_START, wait retry_after_ms and retry; "
    "do not substitute memory-only context. "
    "Expected p99: 10s warm, 30s cold. Recommended client timeout: 60s."
)

_RESEARCH_DESCRIPTION = (
    "Call for complex research questions that need iterative retrieval. "
    "If the result has error_code=KAIRIX_COLD_START, wait retry_after_ms and retry before answering."
)

_CONTRADICT_DESCRIPTION = (
    "Call before writing new facts to check for contradictions against existing knowledge. "
    "If the result has error_code=KAIRIX_COLD_START, wait retry_after_ms and retry before proceeding. "
    "Expected p99: 30s warm, 90s cold. Recommended client timeout: 120s."
)

_BRIEF_DESCRIPTION = (
    "Call when you want a synthesised view of a topic — kairix runs a small research loop "
    "across the knowledge store and returns a structured briefing. "
    "Use it when you'd otherwise be tempted to summarise from memory. "
    "If the result has error_code=KAIRIX_COLD_START, do not summarise from memory; "
    "wait retry_after_ms and retry the same call. "
    "Expected p99: 15s warm, 45s cold. Recommended client timeout: 90s."
)


def _make_prep(ctx: RegistrationContext) -> Callable[..., Any]:
    def prep(
        query: str,
        agent: str | None = None,
        tier: Literal["l0", "l1"] = "l0",
        scope: Scope = DEFAULT_SCOPE,
    ) -> dict[str, Any]:
        """Context preparation: tiered L0/L1 summary generation."""
        if cold := require_ready("prep", ctx.readiness_check):
            return cold
        return tool_prep(query=query, agent=agent, tier=tier, scope=scope)

    return prep


def _make_research(ctx: RegistrationContext) -> Callable[..., Any]:
    def research(query: str, agent: str | None = None, max_turns: int = 4) -> dict[str, Any]:
        """Research a complex question. Searches iteratively until it finds a good answer."""
        if cold := require_ready("research", ctx.readiness_check):
            return cold
        return tool_research(query=query, agent=agent, max_turns=max_turns)

    return research


def _make_contradict(ctx: RegistrationContext) -> Callable[..., Any]:
    def contradict(
        content: str,
        agent: str | None = None,
        top_k: int = 5,
        threshold: float = 0.45,
        top_claims: int = 3,
        scope: Scope = DEFAULT_SCOPE,
    ) -> dict[str, Any]:
        """Check new content against existing knowledge for contradictions."""
        if cold := require_ready("contradict", ctx.readiness_check):
            return cold
        return tool_contradict(
            content=content,
            agent=agent,
            top_k=top_k,
            threshold=threshold,
            top_claims=top_claims,
            scope=scope,
        )

    return contradict


def _make_brief(ctx: RegistrationContext) -> Callable[..., Any]:
    def brief(agent: str) -> dict[str, Any]:
        """Generate a session briefing for an agent. Returns content + on-disk path."""
        if cold := require_ready("brief", ctx.readiness_check):
            return cold
        return tool_brief(agent=agent)

    return brief


BINDINGS: tuple[ToolBinding, ...] = (
    ToolBinding(name="prep", description=_PREP_DESCRIPTION, make=_make_prep, warm_gated=True),
    ToolBinding(name="research", description=_RESEARCH_DESCRIPTION, make=_make_research, warm_gated=True),
    ToolBinding(name="contradict", description=_CONTRADICT_DESCRIPTION, make=_make_contradict, warm_gated=True),
    ToolBinding(name="brief", description=_BRIEF_DESCRIPTION, make=_make_brief, warm_gated=True),
)
