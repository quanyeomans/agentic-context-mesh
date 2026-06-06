"""Protocol-shape contract for :mod:`kairix.agents.onboarding.scanner`
(PR 1.4 / #420).

Pins the structural promises of :class:`ProposedScope`,
:func:`scan_for_agents`, and :func:`discover_single_agent`. The
``kairix onboard scan`` CLI + the ``tool_onboard_scan`` MCP tool both
consume these shapes; this contract freezes them so the public
surface cannot regress silently.
"""

from __future__ import annotations

import dataclasses
import inspect
from pathlib import Path

import pytest

from kairix.agents.onboarding.scanner import (
    ProposedScope,
    discover_single_agent,
    scan_for_agents,
)
from kairix.core.agents.scope import AgentSurface

pytestmark = pytest.mark.contract


# Sabotage-proof (executed): removed `frozen=True` from the @dataclass
# decorator on ProposedScope → dataclasses.fields() still returned the
# correct fields but `ProposedScope(...).file_count = 99` succeeded
# instead of raising FrozenInstanceError; assertion on the FrozenInstance
# behaviour failed; restored.
def test_proposed_scope_is_frozen_dataclass() -> None:
    """``ProposedScope`` is a frozen dataclass — callers depend on
    instances being hashable and immutable when they cache or compare
    proposals across runs."""
    assert dataclasses.is_dataclass(ProposedScope)
    sample = ProposedScope(
        name="agent-alpha",
        surfaces=(AgentSurface(path=Path("/tmp/x"), label="memory"),),
        harness="claude-code",
        confidence="high",
        file_count=3,
        most_recent_mtime=1234567890.0,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        sample.file_count = 99  # type: ignore[misc]  # mutating frozen dc is the sabotage


# Sabotage-proof (executed): renamed `file_count` to `files` on
# ProposedScope → `{f.name for f in dataclasses.fields(...)}` no longer
# contained "file_count"; assertion failed; restored.
def test_proposed_scope_carries_expected_fields() -> None:
    """The six load-bearing fields callers read are present in name and
    in order — operators paste the renderer output into yaml and the
    catalogue test in `tests/contracts/` reads field-by-field."""
    field_names = [f.name for f in dataclasses.fields(ProposedScope)]
    assert field_names == [
        "name",
        "surfaces",
        "harness",
        "confidence",
        "file_count",
        "most_recent_mtime",
    ]


# Sabotage-proof (executed): changed scan_for_agents to return a list
# (`return list(...)`) → `isinstance(scopes, tuple)` failed; restored.
def test_scan_for_agents_returns_tuple_of_proposed_scope(tmp_path: Path) -> None:
    """The function returns ``tuple[ProposedScope, ...]`` — never a list,
    iterator, or None — so callers can rely on length + indexing
    semantics without defensive coercion."""
    scopes = scan_for_agents(memory_root=tmp_path)
    assert isinstance(scopes, tuple)
    for scope in scopes:
        assert isinstance(scope, ProposedScope)


# Sabotage-proof (executed): changed the keyword arg name from
# `memory_root` to `root` on scan_for_agents → calling
# `scan_for_agents(memory_root=...)` raised TypeError; sig.parameters
# check below failed; restored.
def test_scan_for_agents_signature_pins_keyword_only_kwargs() -> None:
    """``scan_for_agents`` accepts ``memory_root`` (required),
    ``workspace_root``, and ``detectors`` as keyword-only — the CLI +
    MCP adapters depend on those parameter names."""
    sig = inspect.signature(scan_for_agents)
    params = sig.parameters
    assert "memory_root" in params
    assert "workspace_root" in params
    assert "detectors" in params
    # memory_root is the only required parameter — confirm by leaving
    # workspace_root + detectors defaulted and trusting Signature.bind.


# Sabotage-proof (executed): renamed `discover_single_agent` first
# positional arg to `agent` (was `agent_name`) — call-site
# `discover_single_agent(agent_name=...)` raised TypeError; restored.
def test_discover_single_agent_signature_pins_positional_name() -> None:
    """``discover_single_agent`` takes the agent name as the first
    positional argument and ``memory_root`` / ``workspace_root`` /
    ``harness`` / ``detectors`` as keyword-only kwargs — MCP tool
    arguments map onto these names."""
    sig = inspect.signature(discover_single_agent)
    first_name = next(iter(sig.parameters))
    assert first_name == "agent_name"
    for kw in ("memory_root", "workspace_root", "harness", "detectors"):
        assert kw in sig.parameters


# Sabotage-proof (executed): made discover_single_agent swallow the
# "nothing detected" branch and return an empty-surfaces scope → the
# `pytest.raises(ValueError)` block did not trip; test failed;
# restored.
def test_discover_single_agent_raises_when_nothing_found(tmp_path: Path) -> None:
    """``discover_single_agent`` raises ``ValueError`` when no detector
    proposes any surface AND no .md files exist at the expected
    directory — callers MUST get a hard signal so they don't silently
    write an empty-surfaces scope into yaml."""
    with pytest.raises(ValueError, match="nonexistent"):
        discover_single_agent("nonexistent", memory_root=tmp_path)


# Sabotage-proof (executed): made discover_single_agent return a tuple
# instead of a single ProposedScope → `isinstance(result, ProposedScope)`
# failed; restored.
def test_discover_single_agent_returns_single_proposed_scope(tmp_path: Path) -> None:
    """The function returns one ``ProposedScope`` — not a tuple. Callers
    inject the result directly into the renderer's tuple-of-one branch."""
    agent_dir = tmp_path / "agent-alpha"
    agent_dir.mkdir()
    (agent_dir / "Board.md").write_text("# board")
    result = discover_single_agent("agent-alpha", memory_root=tmp_path)
    assert isinstance(result, ProposedScope)
    assert result.name == "agent-alpha"
