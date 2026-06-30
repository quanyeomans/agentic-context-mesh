"""pytest-bdd test module for mcp_facts_about.feature.

Step definitions live in ``tests/bdd/steps/mcp_facts_about_steps.py``
and are registered via ``pytest_plugins`` in the root ``conftest.py``.
"""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "mcp_facts_about.feature")

pytestmark = pytest.mark.bdd


@scenario(FEATURE, "Happy path — known entity returns the current facts")
def test_happy_path_known_entity() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "Unknown entity returns an empty list, not an error")
def test_unknown_entity_empty_list() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "A recalled fact carries a re-openable source breadcrumb")
def test_recalled_fact_carries_source_breadcrumb() -> None:
    """Body populated by @scenario from the .feature file."""
