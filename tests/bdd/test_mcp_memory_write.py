"""pytest-bdd test module for mcp_memory_write.feature (#472)."""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "mcp_memory_write.feature")

pytestmark = pytest.mark.bdd


@scenario(FEATURE, "Happy path — a configured agent saves a note and it is on disk")
def test_configured_agent_writes_memory_over_mcp():
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "An unregistered agent is rejected with configuration guidance")
def test_unregistered_agent_is_rejected_with_guidance():
    """Body populated by @scenario from the .feature file."""
