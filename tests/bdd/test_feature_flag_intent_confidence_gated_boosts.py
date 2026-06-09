"""pytest-bdd test module for feature_flag_intent_confidence_gated_boosts.feature."""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "feature_flag_intent_confidence_gated_boosts.feature")

pytestmark = pytest.mark.bdd


@scenario(
    FEATURE,
    "Flag OFF — low-confidence intent still fires the boost",
)
def test_flag_off_low_confidence_still_fires() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(
    FEATURE,
    "Flag ON — low-confidence intent skips the boost",
)
def test_flag_on_low_confidence_skipped() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(
    FEATURE,
    "Flag ON — high-confidence intent still fires the boost",
)
def test_flag_on_high_confidence_fires() -> None:
    """Body populated by @scenario from the .feature file."""
