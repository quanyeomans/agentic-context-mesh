"""Claude Code harness detector (PR 1.3 / #420).

Recognises the markers Claude Code agents typically leave on disk:
``CLAUDE.md``, ``.claude/``, ``AGENTS.md``. When any is present at
``candidate_root`` the detector proposes a ``memory`` surface there.
When a sibling ``workspaces/<agent_name>/`` directory also exists it
proposes an additional ``workspace`` surface.

The implementation uses only :meth:`pathlib.Path.exists` and
:meth:`pathlib.Path.is_dir`, both of which return ``False`` on
non-directory or missing inputs rather than raising — that's how the
"detectors never raise" promise is structurally satisfied.
"""

from __future__ import annotations

from pathlib import Path

from kairix.core.agents.scope import AgentSurface

_MARKERS: tuple[str, ...] = ("CLAUDE.md", ".claude", "AGENTS.md")


class ClaudeCodeDetector:
    """Detect Claude Code-shaped projects under a candidate directory."""

    name: str = "claude-code"

    def propose_surfaces(
        self,
        agent_name: str,
        candidate_root: Path,
    ) -> tuple[AgentSurface, ...]:
        """Return memory + (optional) workspace surfaces for the agent.

        Returns ``()`` when ``candidate_root`` is missing, not a
        directory, or carries none of the Claude Code markers.
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
