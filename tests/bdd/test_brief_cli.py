"""pytest-bdd binding for brief_cli.feature."""

from __future__ import annotations

import pytest
from pytest_bdd import scenario


@pytest.mark.bdd
@scenario("features/brief_cli.feature", "A configured agent is briefed")
def test_brief_configured_agent() -> None:
    """Body populated by @scenario from the .feature file."""


@pytest.mark.bdd
@scenario("features/brief_cli.feature", "An agent with no configured surface is rejected with a helpful stderr")
def test_brief_no_surface_agent() -> None:
    """Body populated by @scenario from the .feature file."""


@pytest.mark.bdd
@scenario("features/brief_cli.feature", "A missing agent argument produces a usage error")
def test_brief_missing_agent() -> None:
    """Body populated by @scenario from the .feature file."""
