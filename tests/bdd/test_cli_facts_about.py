"""pytest-bdd test module for cli_facts_about.feature.

Step definitions live in ``tests/bdd/steps/cli_facts_about_steps.py``
and are registered via ``pytest_plugins`` in the root ``conftest.py``.
"""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "cli_facts_about.feature")

pytestmark = pytest.mark.bdd


@scenario(FEATURE, "Happy path — the command reports the entity's known facts")
def test_happy_path_reports_facts() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "An entity with an indexed summary but no facts still gets an answer")
def test_entity_summary_only() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "An unknown entity reports no facts without failing")
def test_unknown_entity() -> None:
    """Body populated by @scenario from the .feature file."""
