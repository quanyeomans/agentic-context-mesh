"""pytest-bdd binding for mcp_cli.feature."""

from __future__ import annotations

import pytest
from pytest_bdd import scenario


@pytest.mark.bdd
@scenario("features/mcp_cli.feature", "--help lists the serve subcommand")
def test_mcp_help() -> None:
    """Body populated by @scenario from the .feature file."""


@pytest.mark.bdd
@scenario("features/mcp_cli.feature", "serve --help documents every transport choice")
def test_mcp_serve_help() -> None:
    """Body populated by @scenario from the .feature file."""


@pytest.mark.bdd
@scenario("features/mcp_cli.feature", "No subcommand prints help and exits non-zero")
def test_mcp_no_subcommand() -> None:
    """Body populated by @scenario from the .feature file."""


@pytest.mark.bdd
@scenario("features/mcp_cli.feature", "serve rejects an unknown transport via argparse")
def test_mcp_serve_invalid_transport() -> None:
    """Body populated by @scenario from the .feature file."""


@pytest.mark.bdd
@scenario("features/mcp_cli.feature", "serve warns but still starts when the LLM key is the placeholder")
def test_mcp_serve_warns_on_placeholder_key() -> None:
    """Body populated by @scenario from the .feature file."""


@pytest.mark.bdd
@scenario("features/mcp_cli.feature", "serve refuses to start when the neo4j password is empty")
def test_mcp_serve_exits_on_empty_neo4j_password() -> None:
    """Body populated by @scenario from the .feature file."""
