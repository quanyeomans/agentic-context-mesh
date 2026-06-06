"""pytest-bdd binding for entity_enrich.feature (#415)."""

from __future__ import annotations

import pytest
from pytest_bdd import scenario


@pytest.mark.bdd
@scenario("features/entity_enrich.feature", "enrich without a target flag fails with argparse usage error")
def test_entity_enrich_no_target() -> None:
    """Body populated by @scenario from the .feature file."""


@pytest.mark.bdd
@scenario("features/entity_enrich.feature", "enrich --name degrades gracefully without Neo4j")
def test_entity_enrich_name_no_neo4j() -> None:
    """Body populated by @scenario from the .feature file."""


@pytest.mark.bdd
@scenario("features/entity_enrich.feature", "enrich --all-missing degrades gracefully without Neo4j")
def test_entity_enrich_all_missing_no_neo4j() -> None:
    """Body populated by @scenario from the .feature file."""
