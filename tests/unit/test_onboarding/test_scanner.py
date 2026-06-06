"""Unit tests for :mod:`kairix.agents.onboarding.scanner` (PR 1.4 / #420).

``scan_for_agents`` iterates every subdirectory under ``memory_root``
and runs every registered harness detector against it. Each match
becomes one :class:`ProposedScope`. ``discover_single_agent`` does the
same but for a named agent only.

These tests pin the discovery contract — what counts as a "match",
the harness-attribution + confidence rules, the stable name-sorted
ordering, and the no-raise-on-disk-IO guarantee that callers depend
on.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from kairix.agents.onboarding.scanner import (
    ProposedScope,
    discover_single_agent,
    scan_for_agents,
)
from kairix.core.agents.detectors import (
    ClaudeCodeDetector,
    GenericDetector,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# scan_for_agents — empty + smoke
# ---------------------------------------------------------------------------


# Sabotage-proof (executed): made scan_for_agents return
# (ProposedScope(name="phantom", ...),) on an empty root → the empty-
# tuple assertion failed; restored.
def test_empty_memory_root_yields_empty_tuple(tmp_path: Path) -> None:
    """No subdirectories under ``memory_root`` → no proposals. The
    function returns ``()`` rather than raising or returning ``None``
    so the CLI renderer can iterate without guards."""
    scopes = scan_for_agents(memory_root=tmp_path)
    assert scopes == ()


# Sabotage-proof (executed): replaced the os.walk-style child iteration
# with a hardcoded `return ()` → the assertion that two scopes were
# returned failed; restored.
def test_two_agent_subdirs_with_mixed_harnesses_yield_two_scopes(
    tmp_path: Path,
) -> None:
    """A claude-code-shaped subdir + a generic-shaped subdir both
    produce ``ProposedScope`` entries, each tagged with the right
    harness name. Operators rely on the harness label to know which
    config block applies."""
    alpha = tmp_path / "agent-alpha"
    alpha.mkdir()
    (alpha / "CLAUDE.md").write_text("# memory")
    (alpha / "notes.md").write_text("note")

    beta = tmp_path / "agent-beta"
    beta.mkdir()
    (beta / "Board.md").write_text("# board")
    (beta / "more.md").write_text("note")

    scopes = scan_for_agents(memory_root=tmp_path)
    by_name = {s.name: s for s in scopes}
    assert set(by_name) == {"agent-alpha", "agent-beta"}
    assert by_name["agent-alpha"].harness == "claude-code"
    assert by_name["agent-beta"].harness == "generic"


# Sabotage-proof (executed): made the workspace cross-reference branch
# in scan_for_agents always skip the workspace surface → the second
# surface assertion below failed (only one surface returned);
# restored.
def test_workspace_root_cross_reference_adds_workspace_surface(
    tmp_path: Path,
) -> None:
    """When ``workspace_root/<agent>/`` exists, the scanner adds a
    workspace surface alongside the memory surface. Operators want
    both visible in the yaml so writes target the right tree."""
    memory_root = tmp_path / "memory"
    memory_root.mkdir()
    alpha = memory_root / "agent-alpha"
    alpha.mkdir()
    (alpha / "CLAUDE.md").write_text("# memory")

    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()
    (workspace_root / "agent-alpha").mkdir()

    scopes = scan_for_agents(
        memory_root=memory_root,
        workspace_root=workspace_root,
    )
    assert len(scopes) == 1
    surface_labels = [s.label for s in scopes[0].surfaces]
    assert "memory" in surface_labels
    assert "workspace" in surface_labels


# Sabotage-proof (executed): removed the "no workspace_root" branch and
# always appended a workspace surface from a hardcoded path → the
# assertion that only the memory surface was present failed; restored.
def test_no_workspace_root_means_single_surface(tmp_path: Path) -> None:
    """When ``workspace_root`` is None, no workspace surface is
    proposed even if the harness detector would otherwise add one."""
    alpha = tmp_path / "agent-alpha"
    alpha.mkdir()
    (alpha / "CLAUDE.md").write_text("# memory")

    scopes = scan_for_agents(memory_root=tmp_path)
    assert len(scopes) == 1
    assert len(scopes[0].surfaces) == 1
    assert scopes[0].surfaces[0].label == "memory"


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


# Sabotage-proof (executed): removed the `sorted(...)` in scan_for_agents
# and returned subdirs in os.listdir order → the assertion below
# (alphabetical) failed on a tmp_path where listdir returned beta
# before alpha; restored.
def test_scopes_are_returned_sorted_by_name(tmp_path: Path) -> None:
    """Scopes are returned sorted by name — operators reading the
    rendered yaml expect deterministic ordering for diffs."""
    for n in ("agent-gamma", "agent-alpha", "agent-beta"):
        sub = tmp_path / n
        sub.mkdir()
        (sub / "Board.md").write_text("# board")

    scopes = scan_for_agents(memory_root=tmp_path)
    assert [s.name for s in scopes] == [
        "agent-alpha",
        "agent-beta",
        "agent-gamma",
    ]


# ---------------------------------------------------------------------------
# Confidence
# ---------------------------------------------------------------------------


# Sabotage-proof (executed): hardcoded confidence="low" in scan_for_agents
# → the "high"-confidence assertion failed; restored to the conditional.
def test_high_confidence_when_harness_matched_with_md_files(
    tmp_path: Path,
) -> None:
    """``confidence == "high"`` when a harness detector matches AND the
    scope carries .md files — operators copy these blocks unchanged."""
    alpha = tmp_path / "agent-alpha"
    alpha.mkdir()
    (alpha / "CLAUDE.md").write_text("# memory")
    (alpha / "notes.md").write_text("note")

    scopes = scan_for_agents(memory_root=tmp_path)
    assert len(scopes) == 1
    assert scopes[0].confidence == "high"


# Sabotage-proof (executed): made the generic-only branch return
# confidence="high" → the "medium" assertion failed; restored.
def test_medium_confidence_when_only_generic_matched(tmp_path: Path) -> None:
    """``confidence == "medium"`` when only the generic detector
    matched — operators should read the proposal but probably edit
    the surfaces."""
    beta = tmp_path / "agent-beta"
    beta.mkdir()
    (beta / "Board.md").write_text("# board")

    scopes = scan_for_agents(memory_root=tmp_path)
    assert len(scopes) == 1
    assert scopes[0].confidence == "medium"


# Sabotage-proof (executed): treated zero .md files as still "high"
# confidence → the "low" assertion failed; restored.
def test_low_confidence_when_markers_but_no_md_files(tmp_path: Path) -> None:
    """``confidence == "low"`` when a detector matches but the scope
    surfaces carry no .md files — the proposal is structurally
    plausible but operators MUST review before commit."""
    gamma = tmp_path / "agent-gamma"
    gamma.mkdir()
    # marker dir, but no actual .md content
    (gamma / ".claude").mkdir()

    scopes = scan_for_agents(memory_root=tmp_path)
    assert len(scopes) == 1
    assert scopes[0].confidence == "low"


# ---------------------------------------------------------------------------
# file_count + most_recent_mtime
# ---------------------------------------------------------------------------


# Sabotage-proof (executed): replaced the recursive .md count with
# `file_count=0` literal → the assertion `file_count == 2` failed;
# restored.
def test_file_count_counts_md_files_across_surfaces(tmp_path: Path) -> None:
    """``file_count`` is the total .md file count across every surface
    — operators use it as a sanity check against scope size."""
    alpha = tmp_path / "agent-alpha"
    alpha.mkdir()
    (alpha / "CLAUDE.md").write_text("# m")
    (alpha / "deep").mkdir()
    (alpha / "deep" / "note.md").write_text("n")

    scopes = scan_for_agents(memory_root=tmp_path)
    assert len(scopes) == 1
    assert scopes[0].file_count == 2


# Sabotage-proof (executed): replaced the max(mtime) calc with
# `most_recent_mtime = None` → the assertion that the mtime equalled
# the touched file's mtime failed; restored.
def test_most_recent_mtime_reflects_latest_md_file(tmp_path: Path) -> None:
    """``most_recent_mtime`` is the largest .md file mtime across the
    scope — operators use this to see whether the scope is active or
    abandoned."""
    alpha = tmp_path / "agent-alpha"
    alpha.mkdir()
    old = alpha / "old.md"
    old.write_text("o")
    os.utime(old, (1_700_000_000, 1_700_000_000))
    new = alpha / "CLAUDE.md"
    new.write_text("n")
    os.utime(new, (1_800_000_000, 1_800_000_000))

    scopes = scan_for_agents(memory_root=tmp_path)
    assert len(scopes) == 1
    assert scopes[0].most_recent_mtime is not None
    assert scopes[0].most_recent_mtime >= 1_800_000_000.0


# ---------------------------------------------------------------------------
# discover_single_agent
# ---------------------------------------------------------------------------


# Sabotage-proof (executed): removed the no-markers + no-files branch
# raise → the test caught no ValueError; restored.
def test_discover_single_agent_unknown_raises_value_error(
    tmp_path: Path,
) -> None:
    """When the agent dir does not exist AND no detector matches the
    parent root, ``discover_single_agent`` raises ``ValueError`` with
    the agent name in the message."""
    with pytest.raises(ValueError, match="agent-zeta"):
        discover_single_agent("agent-zeta", memory_root=tmp_path)


# Sabotage-proof (executed): removed the `if harness is not None`
# filter → both detectors ran and the harness label came back as
# "claude-code" instead of forced "generic"; restored.
def test_discover_single_agent_harness_filter_isolates_detector(
    tmp_path: Path,
) -> None:
    """When ``harness`` is specified, only that detector runs even if
    others would also have matched."""
    alpha = tmp_path / "agent-alpha"
    alpha.mkdir()
    (alpha / "CLAUDE.md").write_text("# memory")  # would match claude-code
    (alpha / "Board.md").write_text("# board")  # would match generic

    scope = discover_single_agent(
        "agent-alpha",
        memory_root=tmp_path,
        harness="generic",
    )
    assert scope.harness == "generic"


# Sabotage-proof (executed): made discover_single_agent always raise
# ValueError → the happy-path assertion failed; restored.
def test_discover_single_agent_returns_scope_when_md_files_present(
    tmp_path: Path,
) -> None:
    """When .md files exist under the agent's directory but no
    harness matches, the function still returns a ProposedScope
    (generic confidence) — operators get a starting point."""
    alpha = tmp_path / "agent-alpha"
    alpha.mkdir()
    (alpha / "Board.md").write_text("# b")

    scope = discover_single_agent("agent-alpha", memory_root=tmp_path)
    assert isinstance(scope, ProposedScope)
    assert scope.name == "agent-alpha"


# ---------------------------------------------------------------------------
# Disk errors + detector seam
# ---------------------------------------------------------------------------


# Sabotage-proof (executed): removed the try/except wrap around iterdir
# → a FileNotFoundError raised from a missing memory_root broke the
# test (got FileNotFoundError instead of an empty tuple); restored.
def test_disk_errors_swallowed_on_missing_memory_root(tmp_path: Path) -> None:
    """When ``memory_root`` does not exist, the scanner returns ``()``
    rather than raising — the CLI surface should not crash on a
    typo'd path; operators see "no agents found" instead."""
    ghost = tmp_path / "no-such-dir"
    scopes = scan_for_agents(memory_root=ghost)
    assert scopes == ()


# Sabotage-proof (executed): removed the `detectors=detectors` plumbing
# inside scan_for_agents → the explicit-tuple test ran with the
# production registry and saw the generic detector match → harness
# became "generic" not "claude-code"; assertion failed; restored.
def test_detectors_kwarg_is_injection_seam(tmp_path: Path) -> None:
    """``detectors`` is the test seam — when callers pass a custom
    tuple, only those detectors run. Lets the contract layer pin the
    surface without the production registry leaking in."""
    alpha = tmp_path / "agent-alpha"
    alpha.mkdir()
    (alpha / "CLAUDE.md").write_text("# memory")
    (alpha / "Board.md").write_text("# board")  # would also match generic

    # Inject only the claude-code detector → harness must be
    # claude-code (generic is excluded from the registry).
    scopes = scan_for_agents(
        memory_root=tmp_path,
        detectors=(ClaudeCodeDetector(),),
    )
    assert len(scopes) == 1
    assert scopes[0].harness == "claude-code"


# Sabotage-proof (executed): made scan_for_agents iterate files (not
# subdirs) → the assertion that only the directory child was returned
# failed (rogue file appeared); restored.
def test_scan_ignores_top_level_files(tmp_path: Path) -> None:
    """Top-level files under ``memory_root`` are not treated as agent
    candidates — only subdirectories. Operators sometimes drop README.md
    at the memory root."""
    (tmp_path / "README.md").write_text("# top-level readme")
    alpha = tmp_path / "agent-alpha"
    alpha.mkdir()
    (alpha / "Board.md").write_text("# b")

    scopes = scan_for_agents(memory_root=tmp_path)
    assert [s.name for s in scopes] == ["agent-alpha"]


# Sabotage-proof (executed): removed the discover_single_agent
# detectors-kwarg plumbing → the GenericDetector-only call still
# matched claude-code through the registry; assertion failed;
# restored.
def test_discover_single_agent_detectors_kwarg_seam(tmp_path: Path) -> None:
    """``detectors`` kwarg overrides the registry on
    discover_single_agent too — symmetric with scan_for_agents."""
    alpha = tmp_path / "agent-alpha"
    alpha.mkdir()
    (alpha / "Board.md").write_text("# b")

    scope = discover_single_agent(
        "agent-alpha",
        memory_root=tmp_path,
        detectors=(GenericDetector(),),
    )
    assert scope.harness == "generic"
