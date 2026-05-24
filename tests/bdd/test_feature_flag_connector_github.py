"""pytest-bdd test module for feature_flag_connector_github.feature."""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "feature_flag_connector_github.feature")

pytestmark = pytest.mark.bdd


@scenario(FEATURE, "Flag OFF the github connector slot is a no-op")
def test_flag_off_github_noop() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "Flag ON the github connector pipeline runs")
def test_flag_on_github_pipeline() -> None:
    """Body populated by @scenario from the .feature file."""
