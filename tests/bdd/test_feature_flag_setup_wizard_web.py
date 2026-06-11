"""pytest-bdd test module for feature_flag_setup_wizard_web.feature (F54)."""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "feature_flag_setup_wizard_web.feature")

pytestmark = pytest.mark.bdd


@scenario(FEATURE, "Flag ON — the wizard is served")
def test_flag_on_wizard_served() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "Flag OFF — the wizard is absent")
def test_flag_off_wizard_absent() -> None:
    """Body populated by @scenario from the .feature file."""
