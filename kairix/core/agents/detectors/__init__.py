"""Harness-detector framework — proposes :class:`AgentSurface` entries
for an agent by scanning a candidate directory (PR 1.3 / #420).

PR 1.4's ``kairix onboard scan`` iterates every detector returned by
:func:`get_registered_detectors` against each candidate root, then
aggregates the proposals into a draft ``agents.<name>`` config block.

Detectors are stateless and never raise — they return an empty tuple
when nothing is detected so the aggregation loop can concatenate
without None-guarding.
"""

from __future__ import annotations

from kairix.core.agents.detectors.base import HarnessDetector, get_registered_detectors
from kairix.core.agents.detectors.claude_code import ClaudeCodeDetector
from kairix.core.agents.detectors.codex import CodexDetector
from kairix.core.agents.detectors.generic import GenericDetector

__all__ = [
    "ClaudeCodeDetector",
    "CodexDetector",
    "GenericDetector",
    "HarnessDetector",
    "get_registered_detectors",
]
