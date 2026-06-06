"""HarnessDetector Protocol + registry helper (PR 1.3 / #420).

The :class:`HarnessDetector` protocol is the structural contract every
detector satisfies. :func:`get_registered_detectors` returns the
canonical ordered tuple of known detectors with the generic fallback
last — so PR 1.4's aggregation loop can iterate and concatenate
proposals without harness-specific branching.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from kairix.core.agents.scope import AgentSurface


@runtime_checkable
class HarnessDetector(Protocol):
    """Pluggable detector that proposes :class:`AgentSurface` entries
    for an agent by inspecting a candidate directory.

    Detectors are stateless. They never raise on missing dirs or
    non-directory candidates — they return an empty tuple when nothing
    is detected. Callers (PR 1.4's ``kairix onboard scan``) iterate
    every registered detector and aggregate their proposals.
    """

    name: str
    """Canonical harness identifier (e.g. ``"claude-code"`` /
    ``"codex"`` / ``"generic"``). Used in proposal log lines and as
    the ``harness:`` value in the emitted yaml block."""

    def propose_surfaces(
        self,
        agent_name: str,
        candidate_root: Path,
    ) -> tuple[AgentSurface, ...]:
        """Return surfaces this harness recognises for ``agent_name``
        rooted at ``candidate_root``. Empty tuple means "nothing for
        this harness". Never raises."""
        ...


def get_registered_detectors() -> tuple[HarnessDetector, ...]:
    """Return all registered detectors in deterministic order.

    Order matters: PR 1.4's aggregation loop iterates and concatenates,
    treating the ``"generic"`` detector as the harness-agnostic
    fallback at the end of the tuple. Callers can iterate without
    special-casing or sorting.
    """
    # Local import to keep this module import-cycle-free: the detector
    # classes themselves import :class:`HarnessDetector` for the
    # protocol check.
    from kairix.core.agents.detectors.claude_code import ClaudeCodeDetector
    from kairix.core.agents.detectors.codex import CodexDetector
    from kairix.core.agents.detectors.generic import GenericDetector

    return (ClaudeCodeDetector(), CodexDetector(), GenericDetector())
