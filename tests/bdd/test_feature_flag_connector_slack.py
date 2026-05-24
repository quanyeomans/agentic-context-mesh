"""pytest-bdd test module for feature_flag_connector_slack.feature."""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "feature_flag_connector_slack.feature")

pytestmark = pytest.mark.bdd


@scenario(FEATURE, "Flag OFF keeps the legacy single-root hierarchy and the single-cursor list_changes path")
def test_flag_off_legacy_shape() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "Flag ON emits one Container per member channel and walks the hierarchy parent-before-child")
def test_flag_on_per_channel_containers() -> None:
    """Body populated by @scenario from the .feature file."""
