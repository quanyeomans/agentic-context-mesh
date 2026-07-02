"""MCP tool — ``memory_write``: save a memory for an agent (#472).

Wraps the :func:`kairix.use_cases.remember.remember` use case — the
SAME implementation behind ``kairix remember`` — so an agent connected
over MCP can write to its own memory instead of only reading. The tool
validates the agent against the config-driven allowlist, writes a dated
markdown file under the agent's memory surface, and indexes it
immediately so ``search`` finds it in the same session.

Dependency injection:

- ``deps`` is constructor-injected on every call so the tool is
  F1-clean. Production callers leave it ``None`` (the use case wires
  real config / paths / clock / index step); tests pass a
  ``RememberDeps`` built over tmp paths.

Errors:

- Returns a flat envelope with an ``error`` key rather than raising —
  agents read ``error`` to decide whether the call succeeded, and the
  message carries the F21 ``fix:`` / ``next:`` affordance.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any

from kairix.agents.mcp.tools._common import RegistrationContext, ToolBinding
from kairix.use_cases.remember import RememberDeps, remember

__all__ = ["BINDINGS", "tool_memory_write"]


def tool_memory_write(
    agent: str,
    content: str,
    kind: str = "note",
    *,
    deps: RememberDeps | None = None,
) -> dict[str, Any]:
    """Save ``content`` as a memory for ``agent`` and index it for search.

    Parameters
    ----------
    agent:
        Agent name. Must be declared in the operator's ``agents:`` config
        block (or be one of the legacy built-in names).
    content:
        The memory text to save. Empty content is rejected.
    kind:
        One of ``note`` / ``decision`` / ``fact``. Defaults to ``note``.
    deps:
        Optional DI seam — production callers leave it ``None``; tests
        inject a ``RememberDeps`` over tmp paths.

    Returns
    -------
    dict
        ``{"path", "agent", "kind", "classified_as", "indexed", "error",
        "detail"}`` — the frozen :class:`RememberResult` flattened via
        ``dataclasses.asdict`` (same convention as ``ingest_chat``).
        ``error`` is "" on success; on failure it carries an
        F21-actionable message and ``path`` is "".
    """
    result = remember(agent, content, kind=kind, deps=deps)
    return dataclasses.asdict(result)


# ---------------------------------------------------------------------------
# Registration binding — the agent-facing memory-write MCP tool (#472).
# ---------------------------------------------------------------------------

_MEMORY_WRITE_DESCRIPTION = (
    "Save a memory for an agent. Writes the text as a dated markdown file in the "
    "agent's memory folder inside the knowledge store, and indexes it for search. "
    "Works even while kairix is still warming up: the file is always saved, and if "
    "search indexing can't run yet the memory is queued for the next indexing pass. "
    "Use it whenever the agent learns something worth keeping: a note (default), a "
    "decision, or a fact — pass kind to say which. The agent name must be in the "
    "team's agent configuration."
)


def _make_memory_write(ctx: RegistrationContext) -> Callable[..., Any]:
    # PLA-257 — NOT ``warm_gated``, on purpose. An agent records a decision/fact
    # most often at session start, when the embedding model is still warming
    # (tens of seconds). The write doesn't depend on warmth, so gating it would
    # refuse the agent's memory exactly when it most needs to persist. The body
    # always writes the file and BM25-indexes it (a SQLite FTS rebuild —
    # cold-safe); vector embedding follows at the next embed tick. If immediate
    # indexing can't complete, the use case returns a "saved, queued for
    # indexing" status (indexed=False + a re-index affordance) rather than
    # rejecting the write. ``ctx.remember_deps`` is the injection seam
    # (production leaves it None so the use case wires real config / paths).
    def memory_write(
        agent: str,
        content: str,
        kind: str = "note",
    ) -> dict[str, Any]:
        """Write a memory for an agent into the knowledge store."""
        return tool_memory_write(agent=agent, content=content, kind=kind, deps=ctx.remember_deps)

    return memory_write


BINDINGS: tuple[ToolBinding, ...] = (
    ToolBinding(name="memory_write", description=_MEMORY_WRITE_DESCRIPTION, make=_make_memory_write, warm_gated=False),
)
