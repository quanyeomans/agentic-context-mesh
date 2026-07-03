"""pytest-bdd test module for research_synthesis.feature."""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "research_synthesis.feature")


@pytest.mark.bdd
@scenario(FEATURE, "Agent gets a best-effort answer when evidence is incomplete")
def test_low_confidence_synthesis():
    """Body populated by @scenario from the .feature file."""


@pytest.mark.bdd
@scenario(FEATURE, "Research enumerates every item when one source holds a list")
def test_research_enumerates_full_list():
    """Body populated by @scenario from the .feature file."""
