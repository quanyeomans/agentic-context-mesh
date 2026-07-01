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
@scenario(FEATURE, "The agent memory writability probe fails on a read-only overlay")
def test_agent_memory_writable_readonly_fails():
    """Body populated by @scenario from the .feature file."""


@pytest.mark.bdd
@scenario(FEATURE, "The agent memory writability probe passes on a writable overlay")
def test_agent_memory_writable_writable_passes():
    """Body populated by @scenario from the .feature file."""
