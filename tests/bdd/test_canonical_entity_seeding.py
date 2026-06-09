"""pytest-bdd test module for canonical_entity_seeding.feature."""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "canonical_entity_seeding.feature")

pytestmark = pytest.mark.bdd


@scenario(
    FEATURE,
    "A declared canonical entity reaches Neo4j with kairix_canonical=true",
)
def test_canonical_reaches_neo4j_with_canonical_flag() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(
    FEATURE,
    "Aliases declared by the operator land on the Neo4j node",
)
def test_aliases_land_on_node() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(
    FEATURE,
    "A degraded Neo4j leaves zero seeded and the operator can re-run",
)
def test_degraded_neo4j_zero_seeded() -> None:
    """Body populated by @scenario from the .feature file."""
