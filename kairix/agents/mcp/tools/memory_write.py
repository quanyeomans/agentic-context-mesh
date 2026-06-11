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
from typing import Any

from kairix.use_cases.remember import RememberDeps, remember

__all__ = ["tool_memory_write"]


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
