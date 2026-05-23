"""pytest-bdd test module for feature_flag_topology_v2_schema.feature.

Pairs the OFF + ON scenarios with their step implementations under
:mod:`tests.bdd.steps.feature_flag_topology_v2_schema_steps` (registered
in ``tests/conftest.py:pytest_plugins``).
"""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "feature_flag_topology_v2_schema.feature")

pytestmark = pytest.mark.bdd


@scenario(FEATURE, "flag default-off means no production code path populates topology v2 tables")
def test_flag_off_tables_empty() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "flag effective-true unlocks Wave B+ write paths")
def test_flag_on_write_path() -> None:
    """Body populated by @scenario from the .feature file."""
