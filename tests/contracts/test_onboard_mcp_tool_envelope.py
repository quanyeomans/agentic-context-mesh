"""Envelope-shape contract for ``tool_onboard_scan`` + ``tool_onboard_agent``
(PR 1.4 / #420).

Pins the dict shape the MCP tools return so agents can rely on the
keys. Also confirms the capability registry carries one entry per
tool with category ``configuration`` — F25 keeps the registry in
sync with the actual registered tools.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kairix.agents.mcp.server import (
    CAP_CATEGORY_CONFIGURATION,
    tool_capabilities,
    tool_onboard_agent,
    tool_onboard_scan,
)

pytestmark = pytest.mark.contract


def _seed_memory_root(tmp_path: Path) -> Path:
    memory = tmp_path / "memory"
    memory.mkdir()
    alpha = memory / "agent-alpha"
    alpha.mkdir()
    (alpha / "CLAUDE.md").write_text("# memory\n")
    return memory


# Sabotage-proof (executed): swapped the envelope's "agents" key for
# "data" → the assertion below failed because "agents" was missing;
# restored.
def test_tool_onboard_scan_returns_dict_with_agents_key(tmp_path: Path) -> None:
    """``tool_onboard_scan`` returns a dict carrying ``agents`` as the
    list of proposed scope envelopes — agents iterate this list."""
    memory = _seed_memory_root(tmp_path)
    envelope = tool_onboard_scan(memory_root=str(memory))
    assert isinstance(envelope, dict)
    assert "agents" in envelope
    assert isinstance(envelope["agents"], list)


# Sabotage-proof (executed): removed the swallow-and-report wrapping in
# tool_onboard_scan → an os.scandir error on a missing path raised
# instead of being recorded in `error`; test failed; restored.
def test_tool_onboard_scan_swallows_errors_into_envelope() -> None:
    """When ``memory_root`` does not exist, the tool returns an
    envelope with an empty ``agents`` list — never raises, never
    crashes the agent's call site."""
    envelope = tool_onboard_scan(memory_root="/no/such/path/ever/exists")
    assert envelope["agents"] == []
    # error key is always present (even when empty) so agents can
    # branch on it uniformly.
    assert "error" in envelope


# Sabotage-proof (executed): swapped the "agent" key for "data" →
# the assertion below failed; restored.
def test_tool_onboard_agent_returns_dict_with_agent_key(tmp_path: Path) -> None:
    """``tool_onboard_agent`` returns a dict carrying ``agent`` as the
    single proposed scope envelope — agents read ``envelope["agent"]``
    directly."""
    memory = _seed_memory_root(tmp_path)
    envelope = tool_onboard_agent(
        agent_name="agent-alpha",
        memory_root=str(memory),
    )
    assert isinstance(envelope, dict)
    assert "agent" in envelope
    assert envelope["agent"]["name"] == "agent-alpha"


# Sabotage-proof (executed): made tool_onboard_agent re-raise the
# ValueError instead of capturing it into ``error`` → the assertion
# that envelope["error"] carried the agent name failed (uncaught
# ValueError instead); restored.
def test_tool_onboard_agent_swallows_unknown_into_envelope_error(
    tmp_path: Path,
) -> None:
    """Unknown agent → envelope with ``agent: None`` and ``error``
    carrying the name. Never raises."""
    memory = tmp_path / "memory"
    memory.mkdir()
    envelope = tool_onboard_agent(
        agent_name="ghost-agent",
        memory_root=str(memory),
    )
    assert envelope["agent"] is None
    assert "ghost-agent" in envelope["error"]


# Sabotage-proof (executed): dropped the _cap(name="onboard_scan", ...)
# entry from tool_capabilities → the assertion below failed; restored.
def test_capability_registry_carries_onboard_scan_and_onboard_agent() -> None:
    """The capability catalogue exposes both new tools — agents call
    ``tool_capabilities`` to discover what kairix surfaces."""
    cat = tool_capabilities()
    names = {c["name"] for c in cat["capabilities"]}
    assert "onboard_scan" in names
    assert "onboard_agent" in names


# Sabotage-proof (executed): registered the tools under the
# ``diagnostic`` category instead of ``configuration`` → the equality
# assertion failed; restored.
def test_capability_registry_categorises_onboard_tools_as_configuration() -> None:
    """Both new tools live under the ``configuration`` category so
    agents grouping by category can find them together."""
    cat = tool_capabilities()
    by_name = {c["name"]: c for c in cat["capabilities"]}
    assert by_name["onboard_scan"]["category"] == CAP_CATEGORY_CONFIGURATION
    assert by_name["onboard_agent"]["category"] == CAP_CATEGORY_CONFIGURATION
