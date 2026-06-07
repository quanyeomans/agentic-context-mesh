"""Unit tests for :class:`kairix.core.agents.detectors.CodexDetector`
(PR 1.3 / #420).

CodexDetector mirrors :class:`ClaudeCodeDetector` for OpenAI Codex
agents. Recognised markers:

  * ``.codex/`` — settings/memory directory
  * ``AGENTS.md`` — shared agent manifest

Same shape contract as ClaudeCode: memory surface at ``candidate_root``
when any marker is present, plus a workspace surface when a sibling
``workspaces/<agent_name>/`` directory exists.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kairix.core.agents.detectors import CodexDetector
from kairix.core.agents.scope import AgentSurface

pytestmark = pytest.mark.unit


# Sabotage-proof (executed): made CodexDetector.propose_surfaces always
# emit one surface → `out == ()` assertion failed; test failed;
# restored.
def test_empty_dir_yields_empty_tuple(tmp_path: Path) -> None:
    """No markers, no proposal."""
    out = CodexDetector().propose_surfaces("agent-alpha", tmp_path)
    assert out == ()


# Sabotage-proof (executed): dropped `.codex` from the codex marker
# tuple → directory with only `.codex/` considered empty; `len == 1`
# failed; restored.
def test_dot_codex_dir_marker_proposes_memory_surface(tmp_path: Path) -> None:
    """A ``.codex/`` subdirectory triggers a memory surface proposal."""
    (tmp_path / ".codex").mkdir()
    out = CodexDetector().propose_surfaces("agent-alpha", tmp_path)
    assert len(out) == 1
    assert out[0] == AgentSurface(path=tmp_path, glob="**/*.md", label="memory")


# Sabotage-proof (executed): dropped `AGENTS.md` from the codex marker
# tuple → directory considered empty; `len == 1` failed; restored.
def test_agents_md_marker_proposes_memory_surface(tmp_path: Path) -> None:
    """``AGENTS.md`` triggers a memory surface proposal — operators may
    share an agents manifest across codex + claude-code."""
    (tmp_path / "AGENTS.md").write_text("# agents")
    out = CodexDetector().propose_surfaces("agent-alpha", tmp_path)
    assert len(out) == 1
    assert out[0].label == "memory"


# Sabotage-proof (executed): made the codex detector also propose a
# memory surface on CLAUDE.md (cross-harness leak) → `len == 0` failed;
# restored to codex-only markers.
def test_claude_md_alone_does_not_trigger_codex(tmp_path: Path) -> None:
    """A plain `CLAUDE.md` (the claude-code marker) is not a codex
    marker — the codex detector ignores it so the aggregator doesn't
    duplicate proposals across harnesses."""
    (tmp_path / "CLAUDE.md").write_text("# claude only")
    out = CodexDetector().propose_surfaces("agent-alpha", tmp_path)
    assert out == ()


# Sabotage-proof (executed): removed the workspace-exists branch from
# CodexDetector → sibling workspace dir never added; `len == 2` failed;
# restored.
def test_sibling_workspace_dir_adds_workspace_surface(tmp_path: Path) -> None:
    """Sibling ``workspaces/<agent_name>/`` directory → workspace
    surface added after the memory surface."""
    project = tmp_path / "project"
    project.mkdir()
    (project / ".codex").mkdir()
    workspace = tmp_path / "workspaces" / "agent-alpha"
    workspace.mkdir(parents=True)
    out = CodexDetector().propose_surfaces("agent-alpha", project)
    assert len(out) == 2
    assert out[0].label == "memory"
    assert out[1].label == "workspace"
    assert out[1].path == workspace


# Sabotage-proof (executed): made the workspace branch always fire even
# when the directory is absent → `len == 1` failed; restored.
def test_no_sibling_workspace_means_memory_only(tmp_path: Path) -> None:
    """No sibling workspace → memory-only proposal."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "AGENTS.md").write_text("# manifest")
    out = CodexDetector().propose_surfaces("agent-alpha", project)
    assert len(out) == 1
    assert out[0].label == "memory"


# Sabotage-proof (executed): removed the is_dir() check on the
# workspace path so a plain file at workspaces/agent-alpha was treated
# as a workspace → `len == 1` failed; restored.
def test_workspace_must_be_directory_not_file(tmp_path: Path) -> None:
    """A stray file at ``workspaces/<agent_name>`` is not a directory
    and does not produce a workspace surface."""
    project = tmp_path / "project"
    project.mkdir()
    (project / ".codex").mkdir()
    (tmp_path / "workspaces").mkdir()
    (tmp_path / "workspaces" / "agent-alpha").write_text("file masquerading as dir")
    out = CodexDetector().propose_surfaces("agent-alpha", project)
    assert len(out) == 1
    assert out[0].label == "memory"


# Sabotage-proof (executed): swapped the candidate-root guard so a
# file-typed candidate was treated as a directory → marker scan ran on
# a non-dir; `out == ()` assertion failed; restored.
def test_candidate_root_that_is_a_file_yields_empty_tuple(tmp_path: Path) -> None:
    """File candidate_root → empty tuple, no raise."""
    rogue = tmp_path / "not-a-dir.txt"
    rogue.write_text("file")
    out = CodexDetector().propose_surfaces("agent-alpha", rogue)
    assert out == ()


# Sabotage-proof (executed): removed the is_dir guard for missing paths
# → propose_surfaces walked a non-existent path and returned a memory
# surface; `out == ()` failed; restored.
def test_nonexistent_candidate_root_yields_empty_tuple(tmp_path: Path) -> None:
    """Missing candidate_root → empty tuple, no raise."""
    ghost = tmp_path / "ghost"
    out = CodexDetector().propose_surfaces("agent-alpha", ghost)
    assert out == ()


# Sabotage-proof (executed): renamed the detector's `name` attribute
# from "codex" to "openai-codex" → assertion failed; restored.
def test_name_is_codex() -> None:
    """Canonical harness name for the codex detector."""
    assert CodexDetector().name == "codex"
