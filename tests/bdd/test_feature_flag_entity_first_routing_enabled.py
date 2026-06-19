"""pytest-bdd test module for feature_flag_entity_first_routing_enabled.feature."""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "feature_flag_entity_first_routing_enabled.feature")

pytestmark = pytest.mark.bdd


@scenario(
    FEATURE,
    "Flag ON routes the entity summary above a plain note",
)
def test_flag_on_routes_entity_first() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(
    FEATURE,
    "Flag OFF leaves ranking unchanged",
)
def test_flag_off_ranking_unchanged() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(
    FEATURE,
    "Flag ON does not route for a non-entity question",
)
def test_flag_on_non_entity_unchanged() -> None:
    """Body populated by @scenario from the .feature file."""
