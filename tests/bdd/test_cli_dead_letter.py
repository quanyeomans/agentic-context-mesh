"""pytest-bdd test module for cli_dead_letter.feature."""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "cli_dead_letter.feature")

pytestmark = pytest.mark.bdd


@scenario(FEATURE, "Empty dead-letter table — operator sees a friendly empty-state line")
def test_empty_dead_letter_friendly_line() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "Populated dead-letter table — operator sees per-source breakdown")
def test_populated_dead_letter_breakdown() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "JSON output emits the canonical envelope shape")
def test_json_envelope_shape() -> None:
    """Body populated by @scenario from the .feature file."""
