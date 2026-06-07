"""Envelope-shape contract for ``tool_doctor_check_all`` + ``tool_doctor_check_agent``
(PR 1.5 / #420).

Pins the dict shape the MCP tools return so agents can rely on the
keys. Also confirms the capability registry carries one entry per
tool with category ``diagnostic`` — F25 keeps the registry in sync
with the actual registered tools.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kairix.agents.mcp.server import (
    CAP_CATEGORY_DIAGNOSTIC,
    tool_capabilities,
    tool_doctor_check_agent,
    tool_doctor_check_all,
)

pytestmark = pytest.mark.contract


def _seed_config(tmp_path: Path) -> dict[str, object]:
    """Make a one-agent config pointing at a populated surface."""
    surface = tmp_path / "agent-alpha"
    surface.mkdir()
    (surface / "note.md").write_text("# note\n")
    return {
        "agents": {
            "agent-alpha": {
                "harness": "claude-code",
                "surfaces": [
                    {"path": str(surface), "glob": "**/*.md", "label": "memory"},
                ],
            },
        },
    }


# Sabotage-proof (executed): swapped the envelope's "agents" key for
# "data" → the assertion below failed because "agents" was missing;
# restored.
def test_tool_doctor_check_all_returns_dict_with_agents_key(tmp_path: Path) -> None:
    """``tool_doctor_check_all`` returns a dict carrying ``agents`` as
    the list of per-agent health envelopes — agents iterate this list."""
    config = _seed_config(tmp_path)
    envelope = tool_doctor_check_all(config=config)
    assert isinstance(envelope, dict)
    assert "agents" in envelope
    assert isinstance(envelope["agents"], list)
    assert envelope["agents"][0]["name"] == "agent-alpha"


# Sabotage-proof (executed): removed the swallow-and-report wrapping
# in tool_doctor_check_all → an internal error escaped to the caller;
# test failed; restored.
def test_tool_doctor_check_all_swallows_errors_into_envelope() -> None:
    """When config is missing or malformed, the tool returns an
    envelope — never raises, never crashes the agent's call site."""
    envelope = tool_doctor_check_all(config=None)
    # `agents` is always a list (possibly empty); `overall` is always
    # one of the three labels.
    assert isinstance(envelope["agents"], list)
    assert envelope["overall"] in ("ok", "warn", "error")
    assert "error" in envelope


# Sabotage-proof (executed): swapped the "agent" key for "data" → the
# assertion below failed; restored.
def test_tool_doctor_check_agent_returns_dict_with_agent_key(tmp_path: Path) -> None:
    """``tool_doctor_check_agent`` returns a dict carrying ``agent``
    as the single per-agent envelope — agents read ``envelope["agent"]``
    directly."""
    config = _seed_config(tmp_path)
    envelope = tool_doctor_check_agent(
        agent_name="agent-alpha",
        config=config,
    )
    assert isinstance(envelope, dict)
    assert "agent" in envelope
    assert envelope["agent"]["name"] == "agent-alpha"
    assert envelope["agent"]["overall"] == "ok"


# Sabotage-proof (executed): made tool_doctor_check_agent re-raise the
# unknown-agent error instead of capturing it into ``error`` → the
# assertion that the envelope carried the agent name failed
# (uncaught error instead); restored.
def test_tool_doctor_check_agent_unknown_returns_error_envelope() -> None:
    """Unknown agent → envelope with ``agent`` populated as the
    error AgentHealth + ``error`` carrying the message. Never raises."""
    envelope = tool_doctor_check_agent(agent_name="ghost-agent", config={})
    assert envelope["agent"] is not None
    assert envelope["agent"]["overall"] == "error"


# Sabotage-proof (executed): dropped the _cap(name="doctor_check_all", ...)
# entry from tool_capabilities → the assertion below failed; restored.
def test_capability_registry_carries_doctor_check_all_and_agent() -> None:
    """The capability catalogue exposes both new tools — agents call
    ``tool_capabilities`` to discover what kairix surfaces."""
    cat = tool_capabilities()
    names = {c["name"] for c in cat["capabilities"]}
    assert "doctor_check_all" in names
    assert "doctor_check_agent" in names


# Sabotage-proof (executed): registered the tools under the
# ``configuration`` category instead of ``diagnostic`` → the equality
# assertion failed; restored.
def test_capability_registry_categorises_doctor_tools_as_diagnostic() -> None:
    """Both new tools live under the ``diagnostic`` category so
    agents grouping by category can find them together with the other
    health probes."""
    cat = tool_capabilities()
    by_name = {c["name"]: c for c in cat["capabilities"]}
    assert by_name["doctor_check_all"]["category"] == CAP_CATEGORY_DIAGNOSTIC
    assert by_name["doctor_check_agent"]["category"] == CAP_CATEGORY_DIAGNOSTIC
