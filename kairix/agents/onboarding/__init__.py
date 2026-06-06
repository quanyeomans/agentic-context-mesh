"""Agent onboarding — discovery + proposal of agent scopes from disk
(PR 1.4 / #420).

The ``kairix onboard scan`` + ``kairix onboard agent`` subcommands use
:func:`scan_for_agents` and :func:`discover_single_agent` to propose
``agents.<name>`` config blocks for ``kairix.config.yaml``. The MCP
tool surface (``tool_onboard_scan`` / ``tool_onboard_agent`` in
``kairix.agents.mcp.server``) wraps the same Python API so CLI and MCP
return byte-identical envelopes for the same disk state.
"""

from __future__ import annotations

from kairix.agents.onboarding.scanner import (
    ProposedScope,
    discover_single_agent,
    scan_for_agents,
)

__all__ = [
    "ProposedScope",
    "discover_single_agent",
    "scan_for_agents",
]
