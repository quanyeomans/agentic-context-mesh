"""Unit tests for :class:`kairix.core.agents.detectors.GenericDetector`
(PR 1.3 / #420).

GenericDetector is the harness-agnostic fallback. It activates when a
candidate directory contains any markdown file matching one of the
common journal/memory patterns operators use without a Claude Code /
Codex setup:

  * ``YYYY-MM-DD.md`` (date-stamped journal entry)
  * ``Board.md`` / ``MEMORY.md`` / ``decisions.md`` / ``facts.md``
    / ``patterns.md`` / ``rules.md`` (common operator naming)

When activated, proposes only a memory surface at ``candidate_root`` —
the generic detector can't infer where workspaces live so it never
proposes one. The detector is not gated on other harnesses matching;
that aggregation logic lives in PR 1.4's ``kairix onboard scan``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kairix.core.agents.detectors import GenericDetector
from kairix.core.agents.scope import AgentSurface

pytestmark = pytest.mark.unit


# Sabotage-proof (executed): made GenericDetector return one surface
# unconditionally → `out == ()` assertion failed; restored.
def test_empty_dir_yields_empty_tuple(tmp_path: Path) -> None:
    """No markdown, no proposal."""
    out = GenericDetector().propose_surfaces("agent-alpha", tmp_path)
    assert out == ()


# Sabotage-proof (executed): widened the markdown pattern test to match
# .txt files → directory with random.txt produced a proposal; `out == ()`
# failed; restored.
def test_dir_with_random_txt_only_yields_empty_tuple(tmp_path: Path) -> None:
    """A directory containing only non-markdown files (e.g. README.txt)
    does not activate the generic detector."""
    (tmp_path / "random.txt").write_text("not markdown")
    out = GenericDetector().propose_surfaces("agent-alpha", tmp_path)
    assert out == ()


# Sabotage-proof (executed): dropped Board.md from the recognised
# pattern set → directory considered empty; `len == 1` failed;
# restored.
def test_board_md_triggers_memory_surface(tmp_path: Path) -> None:
    """``Board.md`` (a common operator name for a status board) is a
    recognised pattern → memory surface."""
    (tmp_path / "Board.md").write_text("# board")
    out = GenericDetector().propose_surfaces("agent-alpha", tmp_path)
    assert len(out) == 1
    assert out[0] == AgentSurface(path=tmp_path, glob="**/*.md", label="memory")


# Sabotage-proof (executed): dropped MEMORY.md from the recognised
# pattern set → directory considered empty; `len == 1` failed;
# restored.
def test_memory_md_triggers_memory_surface(tmp_path: Path) -> None:
    """``MEMORY.md`` is a recognised pattern → memory surface."""
    (tmp_path / "MEMORY.md").write_text("# memory")
    out = GenericDetector().propose_surfaces("agent-alpha", tmp_path)
    assert len(out) == 1
    assert out[0].label == "memory"


# Sabotage-proof (executed): tightened the date regex to require a
# specific year (`2025-MM-DD`) → 2026 date file no longer matched;
# `len == 1` failed; restored to YYYY-MM-DD generic pattern.
def test_date_stamped_journal_triggers_memory_surface(tmp_path: Path) -> None:
    """A YYYY-MM-DD.md filename (the common journal naming) is a
    recognised pattern → memory surface."""
    (tmp_path / "2026-05-18.md").write_text("# journal entry")
    out = GenericDetector().propose_surfaces("agent-alpha", tmp_path)
    assert len(out) == 1
    assert out[0].label == "memory"


# Sabotage-proof (executed): made GenericDetector skip its scan when a
# CLAUDE.md was present in the directory (gating on no-other-harness
# matching) → assertion `len == 1` failed; restored to harness-agnostic
# behaviour.
def test_generic_runs_even_when_claude_code_markers_present(tmp_path: Path) -> None:
    """The generic detector is not gated on "no other harness matched"
    — that aggregation logic lives in PR 1.4. If a directory has both
    `CLAUDE.md` and a date-stamped journal, generic still proposes."""
    (tmp_path / "CLAUDE.md").write_text("# claude")
    (tmp_path / "2026-05-18.md").write_text("# journal")
    out = GenericDetector().propose_surfaces("agent-alpha", tmp_path)
    assert len(out) == 1
    assert out[0].label == "memory"


# Sabotage-proof (executed): added a workspace surface synthesis path
# to GenericDetector → `len == 1` assertion failed (it returned 2);
# restored to memory-only output.
def test_generic_never_proposes_workspace_surface(tmp_path: Path) -> None:
    """The generic detector can't guess where workspaces live so it
    never proposes a workspace surface — even if a sibling
    ``workspaces/<agent>/`` directory happens to exist."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "Board.md").write_text("# board")
    workspace = tmp_path / "workspaces" / "agent-alpha"
    workspace.mkdir(parents=True)
    out = GenericDetector().propose_surfaces("agent-alpha", project)
    assert len(out) == 1
    assert out[0].label == "memory"


# Sabotage-proof (executed): made the file scan match files in the root
# only and skip the recognised pattern check → "decisions.md" was not
# matched; `len == 1` failed; restored.
def test_decisions_md_triggers_memory_surface(tmp_path: Path) -> None:
    """``decisions.md`` is a recognised pattern → memory surface."""
    (tmp_path / "decisions.md").write_text("# decisions")
    out = GenericDetector().propose_surfaces("agent-alpha", tmp_path)
    assert len(out) == 1
    assert out[0].label == "memory"


# Sabotage-proof (executed): dropped facts.md from the recognised
# pattern set → directory considered empty; `len == 1` failed;
# restored.
def test_facts_md_triggers_memory_surface(tmp_path: Path) -> None:
    """``facts.md`` is a recognised pattern → memory surface."""
    (tmp_path / "facts.md").write_text("# facts")
    out = GenericDetector().propose_surfaces("agent-alpha", tmp_path)
    assert len(out) == 1
    assert out[0].label == "memory"


# Sabotage-proof (executed): dropped patterns.md from the recognised
# pattern set → directory considered empty; `len == 1` failed;
# restored.
def test_patterns_md_triggers_memory_surface(tmp_path: Path) -> None:
    """``patterns.md`` is a recognised pattern → memory surface."""
    (tmp_path / "patterns.md").write_text("# patterns")
    out = GenericDetector().propose_surfaces("agent-alpha", tmp_path)
    assert len(out) == 1
    assert out[0].label == "memory"


# Sabotage-proof (executed): dropped rules.md from the recognised
# pattern set → directory considered empty; `len == 1` failed;
# restored.
def test_rules_md_triggers_memory_surface(tmp_path: Path) -> None:
    """``rules.md`` is a recognised pattern → memory surface."""
    (tmp_path / "rules.md").write_text("# rules")
    out = GenericDetector().propose_surfaces("agent-alpha", tmp_path)
    assert len(out) == 1
    assert out[0].label == "memory"


# Sabotage-proof (executed): swapped the candidate-root guard so a
# file-typed candidate was treated as a directory → the scanner choked
# on a non-dir and propagated an OSError; `out == ()` assertion failed;
# restored to is_dir guard.
def test_candidate_root_that_is_a_file_yields_empty_tuple(tmp_path: Path) -> None:
    """File candidate_root → empty tuple, no raise."""
    rogue = tmp_path / "not-a-dir.txt"
    rogue.write_text("file")
    out = GenericDetector().propose_surfaces("agent-alpha", rogue)
    assert out == ()


# Sabotage-proof (executed): removed the is_dir guard for missing paths
# → propose_surfaces tried to iterdir a non-existent path and raised
# FileNotFoundError; `out == ()` assertion failed; restored.
def test_nonexistent_candidate_root_yields_empty_tuple(tmp_path: Path) -> None:
    """Missing candidate_root → empty tuple, no raise."""
    ghost = tmp_path / "ghost"
    out = GenericDetector().propose_surfaces("agent-alpha", ghost)
    assert out == ()


# Sabotage-proof (executed): renamed the detector's `name` attribute
# from "generic" to "fallback" → assertion failed; restored.
def test_name_is_generic() -> None:
    """Canonical harness name for the fallback detector."""
    assert GenericDetector().name == "generic"


# Sabotage-proof (executed): widened the date regex from \d{4}-\d{2}-\d{2}
# to \d{4}.*\d{2}.*\d{2} → a filename like "2026notes05-18.md" matched;
# the test that expects no match for an off-format name failed; restored
# to strict YYYY-MM-DD.
def test_off_format_date_filename_does_not_trigger(tmp_path: Path) -> None:
    """Filenames that look date-ish but don't fit YYYY-MM-DD (e.g.
    `26-5-18.md`) do not activate the date branch — keeps the recognised
    pattern set predictable for operators."""
    (tmp_path / "26-5-18.md").write_text("# wrong format")
    out = GenericDetector().propose_surfaces("agent-alpha", tmp_path)
    assert out == ()
