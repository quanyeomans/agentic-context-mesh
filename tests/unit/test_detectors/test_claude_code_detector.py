"""Unit tests for :class:`kairix.core.agents.detectors.ClaudeCodeDetector`
(PR 1.3 / #420).

ClaudeCodeDetector scans a candidate directory for the markers Claude Code
agents typically leave behind:

  * ``CLAUDE.md`` — repo-level project memory file
  * ``.claude/`` — settings/memory directory
  * ``AGENTS.md`` — shared agent manifest

When any marker is present the detector proposes a ``memory`` surface at
``candidate_root``. When a sibling ``workspaces/<agent_name>/`` directory
also exists on disk it proposes an additional ``workspace`` surface.
Detectors never raise — they rely only on :meth:`pathlib.Path.exists` and
:meth:`pathlib.Path.is_dir`, which return ``False`` on the filesystem
shapes that would otherwise error, so the PR 1.4 aggregation loop never
trips on a transient permission or non-directory candidate.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kairix.core.agents.detectors import ClaudeCodeDetector
from kairix.core.agents.scope import AgentSurface

pytestmark = pytest.mark.unit


# Sabotage-proof (executed): changed the empty-dir guard in
# ClaudeCodeDetector.propose_surfaces to always return one surface →
# `out == ()` assertion failed; test failed; restored.
def test_empty_dir_yields_empty_tuple(tmp_path: Path) -> None:
    """Candidate directory with no recognised markers → empty proposal.
    Detectors over-propose noise otherwise."""
    out = ClaudeCodeDetector().propose_surfaces("agent-alpha", tmp_path)
    assert out == ()


# Sabotage-proof (executed): removed the `CLAUDE.md` marker from the
# marker list inside ClaudeCodeDetector → the test directory was
# considered empty and the assertion `len(out) == 1` failed; restored.
def test_claude_md_marker_proposes_memory_surface(tmp_path: Path) -> None:
    """``CLAUDE.md`` at the candidate root → memory surface at the same
    root with the canonical ``**/*.md`` glob and ``"memory"`` label."""
    (tmp_path / "CLAUDE.md").write_text("# project memory")
    out = ClaudeCodeDetector().propose_surfaces("agent-alpha", tmp_path)
    assert len(out) == 1
    assert out[0] == AgentSurface(path=tmp_path, glob="**/*.md", label="memory")


# Sabotage-proof (executed): removed the `.claude` marker check → empty
# tuple returned for a dir that only had .claude/; assertion failed;
# restored.
def test_dot_claude_dir_marker_proposes_memory_surface(tmp_path: Path) -> None:
    """A ``.claude/`` subdirectory alone is enough to activate the
    detector — operators with `.claude/settings.json` but no top-level
    `CLAUDE.md` still get a memory surface."""
    (tmp_path / ".claude").mkdir()
    out = ClaudeCodeDetector().propose_surfaces("agent-alpha", tmp_path)
    assert len(out) == 1
    assert out[0].label == "memory"
    assert out[0].path == tmp_path


# Sabotage-proof (executed): dropped `AGENTS.md` from the marker tuple →
# directory considered empty, assertion `len == 1` failed; restored.
def test_agents_md_marker_proposes_memory_surface(tmp_path: Path) -> None:
    """``AGENTS.md`` (the shared multi-agent manifest) activates the
    detector even without a `CLAUDE.md`."""
    (tmp_path / "AGENTS.md").write_text("# agents manifest")
    out = ClaudeCodeDetector().propose_surfaces("agent-alpha", tmp_path)
    assert len(out) == 1
    assert out[0].label == "memory"


# Sabotage-proof (executed): removed the workspace-exists branch from
# ClaudeCodeDetector → the sibling workspace dir was never proposed;
# `len == 2` assertion failed; restored.
def test_sibling_workspace_dir_adds_workspace_surface(tmp_path: Path) -> None:
    """When a sibling ``workspaces/<agent_name>/`` directory exists, the
    detector proposes both a memory surface and a workspace surface in
    that order."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "CLAUDE.md").write_text("# memory")
    workspace = tmp_path / "workspaces" / "agent-alpha"
    workspace.mkdir(parents=True)
    out = ClaudeCodeDetector().propose_surfaces("agent-alpha", project)
    assert len(out) == 2
    assert out[0].label == "memory"
    assert out[0].path == project
    assert out[1].label == "workspace"
    assert out[1].path == workspace


# Sabotage-proof (executed): made the workspace branch fire even when the
# directory was absent → assertion `len == 1` failed; restored.
def test_no_sibling_workspace_means_memory_only(tmp_path: Path) -> None:
    """Without a sibling ``workspaces/<agent_name>/`` directory the
    detector proposes only the memory surface — phantom workspace
    surfaces inflate the aggregation in PR 1.4."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "CLAUDE.md").write_text("# memory")
    # NOTE: no workspaces/agent-alpha created
    out = ClaudeCodeDetector().propose_surfaces("agent-alpha", project)
    assert len(out) == 1
    assert out[0].label == "memory"


# Sabotage-proof (executed): made the workspace check accept a regular
# file instead of requiring a directory → a stray
# `workspaces/agent-alpha` file would be proposed as a surface;
# assertion that the proposal carried only the memory surface failed;
# restored to is_dir().
def test_workspace_must_be_directory_not_file(tmp_path: Path) -> None:
    """A file called ``workspaces/agent-alpha`` (not a directory) does
    not satisfy the workspace check — operators shouldn't see a phantom
    workspace surface backed by a stray file."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "CLAUDE.md").write_text("# memory")
    (tmp_path / "workspaces").mkdir()
    (tmp_path / "workspaces" / "agent-alpha").write_text("not a dir")
    out = ClaudeCodeDetector().propose_surfaces("agent-alpha", project)
    assert len(out) == 1
    assert out[0].label == "memory"


# Sabotage-proof (executed): changed the candidate-root guard to skip the
# is_dir check (assume the path is a dir) → propose_surfaces returned a
# memory surface anchored at the file path; `out == ()` failed; restored.
def test_candidate_root_that_is_a_file_yields_empty_tuple(tmp_path: Path) -> None:
    """When ``candidate_root`` is not a directory (file, symlink to
    nowhere, missing entirely) the detector returns an empty tuple
    without raising — the F1.4 aggregation loop must tolerate operator
    typos in the scan list."""
    rogue = tmp_path / "not-a-dir.txt"
    rogue.write_text("this is a file, not a directory")
    out = ClaudeCodeDetector().propose_surfaces("agent-alpha", rogue)
    assert out == ()


# Sabotage-proof (executed): swapped the candidate-root guard to
# `if candidate_root.is_dir()` return early (no markers checked) → the
# function returned an empty tuple even when CLAUDE.md was present;
# `len == 1` assertion failed; restored.
def test_nonexistent_candidate_root_yields_empty_tuple(tmp_path: Path) -> None:
    """When ``candidate_root`` does not exist the detector returns an
    empty tuple without raising."""
    ghost = tmp_path / "no-such-directory"
    out = ClaudeCodeDetector().propose_surfaces("agent-alpha", ghost)
    assert out == ()


# Sabotage-proof (executed): renamed the detector's `name` attribute from
# "claude-code" to "claude" → assertion failed; restored.
def test_name_is_claude_code() -> None:
    """The detector reports the canonical harness name used in proposal
    log lines + the eventual yaml ``harness:`` field."""
    assert ClaudeCodeDetector().name == "claude-code"
