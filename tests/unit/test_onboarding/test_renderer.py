"""Unit tests for :mod:`kairix.agents.onboarding.renderer` (PR 1.4 / #420).

Pins the operator-facing output formats: the YAML block (paste-ready
into ``kairix.config.yaml``), the human-readable text report, and the
per-agent validation report. These are the bits operators read and
copy — so the tests assert on the strings, not on internal helpers.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from kairix.agents.onboarding.renderer import (
    render_scopes_as_text,
    render_scopes_as_yaml,
    render_validation_report,
)
from kairix.agents.onboarding.scanner import ProposedScope
from kairix.core.agents.scope import AgentSurface

pytestmark = pytest.mark.unit


def _sample_scope(name: str = "agent-alpha") -> ProposedScope:
    return ProposedScope(
        name=name,
        surfaces=(
            AgentSurface(path=Path("/srv/memory") / name, glob="**/*.md", label="memory"),
            AgentSurface(path=Path("/srv/workspaces") / name, glob="**/*.md", label="workspace"),
        ),
        harness="claude-code",
        confidence="high",
        file_count=12,
        most_recent_mtime=1_750_000_000.0,
    )


# ---------------------------------------------------------------------------
# render_scopes_as_yaml
# ---------------------------------------------------------------------------


# Sabotage-proof (executed): made render_scopes_as_yaml emit a top-level
# `agent: <name>` (singular) instead of `agents:` mapping → the
# yaml.safe_load round-trip's `"agents" in parsed` assertion failed;
# restored.
def test_render_yaml_round_trips_through_safe_load() -> None:
    """The rendered YAML is parseable by ``yaml.safe_load`` and
    surfaces the ``agents:`` top-level key the config loader expects."""
    out = render_scopes_as_yaml((_sample_scope(),))
    parsed = yaml.safe_load(out)
    assert isinstance(parsed, dict)
    assert "agents" in parsed
    assert "agent-alpha" in parsed["agents"]


# Sabotage-proof (executed): made render_scopes_as_yaml emit
# `harness: unknown` literal → the round-trip assertion that
# parsed["agents"]["agent-alpha"]["harness"] == "claude-code" failed;
# restored.
def test_render_yaml_carries_harness_field() -> None:
    """Each agent block carries the ``harness:`` field — the config
    loader stores this on the AgentScope."""
    out = render_scopes_as_yaml((_sample_scope(),))
    parsed = yaml.safe_load(out)
    assert parsed["agents"]["agent-alpha"]["harness"] == "claude-code"


# Sabotage-proof (executed): hardcoded a single empty surfaces list in
# render_scopes_as_yaml → the assertion that two surfaces survived the
# round-trip failed (len == 0 not 2); restored.
def test_render_yaml_carries_every_surface_with_path_glob_label() -> None:
    """Every surface in the scope appears in the YAML with path, glob,
    and label populated — operators paste the block unchanged."""
    out = render_scopes_as_yaml((_sample_scope(),))
    parsed = yaml.safe_load(out)
    surfaces = parsed["agents"]["agent-alpha"]["surfaces"]
    assert isinstance(surfaces, list)
    assert len(surfaces) == 2
    for s in surfaces:
        assert "path" in s
        assert "glob" in s
        assert "label" in s


# Sabotage-proof (executed): stripped the `# confidence=...` comment
# from render_scopes_as_yaml → the substring assertion below failed;
# restored.
def test_render_yaml_includes_confidence_comment() -> None:
    """The renderer prefixes each agent block with a one-line comment
    showing confidence + file count — operators read confidence first
    when deciding whether to keep the block as-is."""
    out = render_scopes_as_yaml((_sample_scope(),))
    assert "confidence=high" in out
    assert "file_count=12" in out


# Sabotage-proof (executed): made render_scopes_as_yaml return the
# empty string for an empty tuple → the assertion below failed because
# the parsed result was None, not {"agents": {}}; restored.
def test_render_yaml_empty_input_yields_empty_agents_mapping() -> None:
    """Empty scopes → an ``agents: {}`` placeholder block. Operators
    see "no proposals" without having to parse around None."""
    out = render_scopes_as_yaml(())
    parsed = yaml.safe_load(out)
    assert parsed == {"agents": {}}


# ---------------------------------------------------------------------------
# render_scopes_as_text
# ---------------------------------------------------------------------------


# Sabotage-proof (executed): removed the agent.name f-string from
# render_scopes_as_text → the assertion that "agent-alpha" appeared
# in stdout failed; restored.
def test_render_text_lists_each_scope_name(tmp_path: Path) -> None:
    """The text format lists every scope by name — operators scan
    the list before deciding which to keep."""
    _ = tmp_path  # F19: unused param prefix (pytest fixture cleanup hook)
    out = render_scopes_as_text((_sample_scope(),))
    assert "agent-alpha" in out


# Sabotage-proof (executed): replaced f"{file_count}" with f"{0}" →
# the assertion that file_count appeared in stdout failed; restored.
def test_render_text_includes_file_count() -> None:
    """The text format includes the per-scope file count — operators
    use it as a sanity check."""
    out = render_scopes_as_text((_sample_scope(),))
    assert "12" in out


# Sabotage-proof (executed): made render_scopes_as_text emit a
# placeholder "(unknown)" instead of the harness name → the assertion
# that "claude-code" appeared failed; restored.
def test_render_text_includes_harness() -> None:
    """The text format includes the harness name so operators know
    which config block applies."""
    out = render_scopes_as_text((_sample_scope(),))
    assert "claude-code" in out


# Sabotage-proof (executed): hardcoded the empty-input branch to
# return "" → the assertion that some "No agents" phrasing appeared
# failed; restored.
def test_render_text_empty_input_says_no_agents() -> None:
    """Empty scopes → a "no agents found" line so operators don't
    misread an empty stdout as a crash."""
    out = render_scopes_as_text(())
    assert "no agents" in out.lower()


# ---------------------------------------------------------------------------
# render_validation_report
# ---------------------------------------------------------------------------


# Sabotage-proof (executed): made render_validation_report return ""
# unconditionally → the assertion that "agent-alpha" appeared in
# stdout failed; restored.
def test_render_validation_report_lists_each_agent() -> None:
    """The validation report lists every agent's file count + recency —
    operators read this before commit to confirm the scope is real."""
    out = render_validation_report((_sample_scope(),))
    assert "agent-alpha" in out
    assert "12" in out


# Sabotage-proof (executed): made render_validation_report skip the
# most_recent_mtime block → the substring assertion that the rendered
# date appeared failed; restored.
def test_render_validation_report_renders_most_recent_mtime_as_iso_date() -> None:
    """The most-recent .md mtime is rendered as an ISO date —
    operators read "2025-06" not "1750000000.0"."""
    out = render_validation_report((_sample_scope(),))
    # 1_750_000_000 → 2025-06-15 (or so). The renderer must emit YYYY-MM-DD.
    assert "2025" in out
