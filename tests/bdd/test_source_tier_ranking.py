"""pytest-bdd test module for source_tier_ranking.feature."""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "source_tier_ranking.feature")

pytestmark = pytest.mark.bdd


@scenario(FEATURE, "Canonical collection outranks reference collection on tie")
def test_canonical_outranks_reference() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "With the boost disabled, tier mapping has no effect")
def test_disabled_boost_no_multiplier() -> None:
    """Body populated by @scenario from the .feature file."""
