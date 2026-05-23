"""pytest-bdd test module for cli_features.feature."""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "cli_features.feature")

pytestmark = pytest.mark.bdd


@scenario(FEATURE, 'Empty registry — operator sees the friendly "no flags" line')
def test_empty_registry_friendly_line() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "JSON output emits the canonical envelope shape")
def test_json_output_envelope_shape() -> None:
    """Body populated by @scenario from the .feature file."""
