"""pytest-bdd test module for mcp_dead_letter_status.feature."""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "mcp_dead_letter_status.feature")

pytestmark = pytest.mark.bdd


@scenario(FEATURE, "Empty dead-letter table — agent sees a zero-row envelope")
def test_empty_envelope() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "Populated dead-letter table — agent sees the structured envelope")
def test_populated_envelope() -> None:
    """Body populated by @scenario from the .feature file."""
