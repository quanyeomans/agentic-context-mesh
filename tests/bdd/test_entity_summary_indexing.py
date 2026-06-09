"""pytest-bdd test module for entity_summary_indexing.feature."""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "entity_summary_indexing.feature")

pytestmark = pytest.mark.bdd


@scenario(
    FEATURE,
    "Description-keyword query surfaces an enriched entity",
)
def test_description_keyword_query_surfaces_entity() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(
    FEATURE,
    "Description-keyword query returns no entity row when the flag is off",
)
def test_description_keyword_query_returns_no_entity_when_flag_off() -> None:
    """Body populated by @scenario from the .feature file."""
