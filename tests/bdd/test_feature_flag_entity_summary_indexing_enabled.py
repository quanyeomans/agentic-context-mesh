"""pytest-bdd test module for feature_flag_entity_summary_indexing_enabled.feature."""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "feature_flag_entity_summary_indexing_enabled.feature")

pytestmark = pytest.mark.bdd


@scenario(
    FEATURE,
    "Flag OFF — the projector never ticks",
)
def test_flag_off_projector_not_ticked() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(
    FEATURE,
    "Flag ON — the projector ticks once per worker tick",
)
def test_flag_on_projector_ticked_once() -> None:
    """Body populated by @scenario from the .feature file."""
