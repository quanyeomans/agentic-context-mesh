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
    "A first-party entity with no Wikidata id still surfaces by its description",
)
def test_first_party_entity_without_qid_surfaces_by_description() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(
    FEATURE,
    "Description-keyword query returns no entity row when the flag is off",
)
def test_description_keyword_query_returns_no_entity_when_flag_off() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(
    FEATURE,
    "Operator sees a Wikidata badge on entity rows in CLI output",
)
def test_operator_sees_wikidata_badge_on_entity_rows() -> None:
    """Body populated by @scenario from the .feature file."""
