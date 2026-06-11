"""pytest-bdd test module for cli_remember.feature (#472)."""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "cli_remember.feature")

pytestmark = pytest.mark.bdd


@scenario(FEATURE, "A configured agent saves a decision and gets back where it landed")
def test_configured_agent_saves_a_decision():
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "A built-in agent keeps working without any configuration")
def test_builtin_agent_works_without_config():
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "An unknown agent is told how to get configured")
def test_unknown_agent_gets_configuration_guidance():
    """Body populated by @scenario from the .feature file."""
