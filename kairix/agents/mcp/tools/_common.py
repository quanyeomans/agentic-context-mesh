"""Shared registration framework + cross-domain vocabulary for the per-domain
MCP tool adapters.

The FastMCP server (:mod:`kairix.agents.mcp.server`) registers its tools by
walking :data:`kairix.agents.mcp.server.CAPABILITIES_CATALOG`: for each
catalogue row it looks up the matching :class:`ToolBinding` supplied by a
per-domain adapter module (``retrieval`` / ``synthesis`` / ``orient`` /
``diagnostic`` / ``operator`` and the agent-write adapters ``ingest_chat`` /
``facts_about`` / ``memory_write``) and registers it. Each binding carries the
registered tool's description, its warm-gate flag, and a ``make(ctx)`` factory
that returns the correctly-signed body closure — so the transport concerns
(cold-start gate, deps threading, readiness callbacks) travel with the domain
that owns the tool, while ``server.py`` stays a thin registration walk.

This module holds ONLY the vocabulary shared across those adapters. It imports
no adapter and no ``server`` internals, so every adapter imports from here
without an import cycle.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from kairix.core.search.scope import Scope

# Default retrieval scope — every retrieval/synthesis tool signature defaults to
# the shared-agent scope so an un-scoped call reads the team-shared surface.
DEFAULT_SCOPE: Scope = Scope.SHARED_AGENT

# Canonical runbook reference for every operator-only escalation envelope AND the
# capability catalogue's ``see_also``. Single-sourced here so the escalation
# stubs and the catalogue point at the same runbook without literal drift.
RETRIEVAL_RUNBOOK = "docs/runbooks/kairix-retrieval-health.md"

# Agent-safe caps for the probe surface — exceeding either dimension routes the
# call into the operator-only escalation envelope instead of running.
# Rationale: 20 queries at ~300 ms with concurrency 3 stays under ~6 s wallclock
# and matches typical teaming load. Anything bigger stresses the system enough
# that an operator should be in the loop.
MCP_PROBE_QUERIES_CAP = 20
MCP_PROBE_CONCURRENCY_CAP = 3


@dataclass(frozen=True)
class RegistrationContext:
    """Build-time context threaded to each :class:`ToolBinding` factory.

    ``build_server`` constructs one of these per server and passes it to every
    binding's ``make`` factory, so a closure can capture exactly the seams it
    needs (the HTTP-deployment readiness gate, the readiness-open callback, or
    the agent-write DI seams) without ``server.py`` hand-writing per-tool
    wiring. Production leaves every field ``None``; an integration test threads
    a readiness stub or a tmp-path deps object.
    """

    readiness_check: Callable[[], bool] | None = None
    mark_ready: Callable[[], None] | None = None
    # ``RememberDeps | None`` — typed ``Any`` to keep this module free of the
    # heavyweight use-case import at parse time.
    remember_deps: Any = None
    # ``FactsAboutDeps | None`` — same rationale.
    facts_about_deps: Any = None


@dataclass(frozen=True)
class ToolBinding:
    """One registered MCP tool, expressed as catalogue-walkable data.

    ``server.py`` maps each ``CAPABILITIES_CATALOG`` row to its binding (by the
    row's ``mcp_tool`` for agent-callable tools, or its ``escalate_via`` name
    for operator-only stubs) and registers it: it builds the body via
    ``make(ctx)``, wraps it in ``@warm_gate`` when ``warm_gated`` is set, then
    ``@async_tool_handler``, then ``@server.tool(description=...)``. Keeping the
    description + gate as data (not a hand-written decorator stack) is what lets
    the ~38 registrations derive from the catalogue.

    Attributes:
        name: The registered MCP tool name — matches the catalogue row's
            ``mcp_tool`` (agent-callable) or ``escalate_via`` (operator stub).
        description: The tool description an LLM agent sees, or ``None`` to fall
            back to the body closure's docstring (FastMCP uses ``fn.__doc__``).
        make: Factory that, given the :class:`RegistrationContext`, returns the
            correctly-signed body closure (its ``__name__`` MUST equal ``name``
            so FastMCP registers it under the right tool name).
        warm_gated: When ``True`` the body is wrapped in ``@warm_gate`` so it
            returns the ColdStart envelope while kairix is still warming.
    """

    name: str
    description: str | None
    make: Callable[[RegistrationContext], Callable[..., Any]]
    warm_gated: bool = False


def operator_only_envelope(
    capability: str,
    operator_command: str,
    reason: str,
    expected_runtime_seconds: int,
    see_also: list[str] | None = None,
) -> dict[str, Any]:
    """Structured escalation envelope for an operator-only capability.

    Agents that call a capability which takes minutes, mutates state, or is a
    destructive recovery action receive this envelope naming the exact CLI
    command to surface to their admin, so they can escalate rather than guess.
    """
    return {
        "error": "OperatorOnlyCapability",
        "capability": capability,
        "reason": reason,
        "operator_command": operator_command,
        "expected_runtime_seconds": expected_runtime_seconds,
        "see_also": see_also or [],
    }
