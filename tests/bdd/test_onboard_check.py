"""pytest-bdd test module for onboard_check.feature."""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "onboard_check.feature")


@pytest.mark.bdd
@scenario(FEATURE, "All checks pass on a configured instance")
def test_all_checks_pass():
    """Body populated by @scenario from the .feature file."""


@pytest.mark.bdd
@scenario(FEATURE, "Missing credentials are detected")
def test_missing_credentials():
    """Body populated by @scenario from the .feature file."""


@pytest.mark.bdd
@scenario(FEATURE, "The agent memory writability probe passes on a writable overlay")
def test_agent_memory_writable_writable_passes():
    """Body populated by @scenario from the .feature file."""


@pytest.mark.bdd
@scenario(FEATURE, "A read-only overlay with a writable fallback passes the deploy gate")
def test_agent_memory_writable_readonly_fallback_passes():
    """Body populated by @scenario from the .feature file."""


@pytest.mark.bdd
@scenario(FEATURE, "An agent with no writable destination anywhere fails the deploy gate")
def test_agent_memory_writable_nowhere_writable_fails():
    """Body populated by @scenario from the .feature file."""
