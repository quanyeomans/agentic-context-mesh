"""pytest-bdd test module for setup_wizard_source_oauth.feature (#489)."""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "setup_wizard_source_oauth.feature")

pytestmark = pytest.mark.bdd


@scenario(FEATURE, "An operator connects a chat workspace and picks channels")
def test_connect_workspace_and_pick_channels() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "A cancelled consent screen explains what happened")
def test_cancelled_consent_explains() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "A stray sign-in response is turned away")
def test_stray_callback_turned_away() -> None:
    """Body populated by @scenario from the .feature file."""
