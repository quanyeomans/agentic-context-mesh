"""OpenAI Codex harness detector (PR 1.3 / #420).

Mirrors :class:`~kairix.core.agents.detectors.claude_code.ClaudeCodeDetector`
for Codex-shaped projects. Recognised markers: ``.codex/`` and
``AGENTS.md``. The ``AGENTS.md`` marker is shared with Claude Code on
purpose — operators may ship a single manifest for both harnesses and
both detectors should report a memory surface so the PR 1.4 aggregator
can dedupe at the boundary.
"""

from __future__ import annotations

from pathlib import Path

from kairix.core.agents.scope import AgentSurface

_MARKERS: tuple[str, ...] = (".codex", "AGENTS.md")


class CodexDetector:
    """Detect Codex-shaped projects under a candidate directory."""

    name: str = "codex"

    def propose_surfaces(
        self,
        agent_name: str,
        candidate_root: Path,
    ) -> tuple[AgentSurface, ...]:
        """Return memory + (optional) workspace surfaces for the agent.

        Returns ``()`` when ``candidate_root`` is missing, not a
        directory, or carries none of the Codex markers.
        """
        if not candidate_root.is_dir():
            return ()
        if not any((candidate_root / marker).exists() for marker in _MARKERS):
            return ()

        surfaces: list[AgentSurface] = [
            AgentSurface(path=candidate_root, glob="**/*.md", label="memory"),
        ]
        workspace = candidate_root.parent / "workspaces" / agent_name
        if workspace.is_dir():
            surfaces.append(AgentSurface(path=workspace, glob="**/*.md", label="workspace"))
        return tuple(surfaces)
