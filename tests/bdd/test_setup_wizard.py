"""pytest-bdd test module for setup_wizard.feature."""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "setup_wizard.feature")

pytestmark = pytest.mark.bdd


@scenario(FEATURE, "Operator completes the full setup journey")
def test_full_setup_journey() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "A rejected provider key shows guidance instead of jargon")
def test_rejected_key_shows_guidance() -> None:
    """Body populated by @scenario from the .feature file."""
