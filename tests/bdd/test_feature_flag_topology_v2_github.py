"""pytest-bdd test module for feature_flag_topology_v2_github.feature."""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "feature_flag_topology_v2_github.feature")

pytestmark = pytest.mark.bdd


@scenario(FEATURE, "Flag OFF the github connector retains the legacy single-cursor shape")
def test_flag_off_legacy_shape() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "Flag ON the github connector emits per-repository containers")
def test_flag_on_per_repo_containers() -> None:
    """Body populated by @scenario from the .feature file."""
