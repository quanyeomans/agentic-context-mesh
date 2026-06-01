"""pytest-bdd test module for feature_flag_topology_v2_default_in_scope.feature.

Pairs the OFF + ON scenarios with their step implementations under
:mod:`tests.bdd.steps.feature_flag_topology_v2_default_in_scope_steps`
(registered in ``tests/conftest.py:pytest_plugins``).
"""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "feature_flag_topology_v2_default_in_scope.feature")

pytestmark = pytest.mark.bdd


@scenario(
    FEATURE,
    "Flag OFF returns every read-eligible scope entry (back-compat)",
)
def test_flag_off_returns_every_read_eligible_entry() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(
    FEATURE,
    "Flag ON filters default search to the in-default subset only",
)
def test_flag_on_filters_to_in_default_subset() -> None:
    """Body populated by @scenario from the .feature file."""
