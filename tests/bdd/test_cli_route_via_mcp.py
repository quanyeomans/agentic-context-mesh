"""pytest-bdd binding for cli_route_via_mcp.feature (#411)."""

from __future__ import annotations

import pytest
from pytest_bdd import scenario


@pytest.mark.bdd
@scenario("features/cli_route_via_mcp.feature", "Routes through MCP when the server is responsive")
def test_routes_via_mcp_when_responsive() -> None:
    """Body populated by @scenario from the .feature file."""


@pytest.mark.bdd
@scenario("features/cli_route_via_mcp.feature", "Falls back to in-process when the MCP server is not responsive")
def test_falls_back_when_unresponsive() -> None:
    """Body populated by @scenario from the .feature file."""


@pytest.mark.bdd
@scenario("features/cli_route_via_mcp.feature", "Subcommands without an MCP equivalent stay in-process")
def test_unmapped_subcommand_stays_in_process() -> None:
    """Body populated by @scenario from the .feature file."""


@pytest.mark.bdd
@scenario("features/cli_route_via_mcp.feature", "Operator can disable routing globally")
def test_routing_disabled() -> None:
    """Body populated by @scenario from the .feature file."""
