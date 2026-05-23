"""pytest-bdd test module for mcp_features_status.feature."""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "mcp_features_status.feature")

pytestmark = pytest.mark.bdd


@scenario(FEATURE, "Live registry returns an envelope with the flags list populated")
def test_live_registry_envelope() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "Envelope shape mirrors the CLI --json output")
def test_envelope_shape_mirrors_cli_json() -> None:
    """Body populated by @scenario from the .feature file."""
