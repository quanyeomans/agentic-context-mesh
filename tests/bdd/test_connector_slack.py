"""pytest-bdd test module for connector_slack.feature."""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "connector_slack.feature")

pytestmark = pytest.mark.bdd


@scenario(FEATURE, "A new message in a public channel surfaces as a created change event")
def test_happy_path_message_to_event() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "A DM message surfaces with the personal sensitivity tier")
def test_dm_personal_sensitivity() -> None:
    """Body populated by @scenario from the .feature file."""
