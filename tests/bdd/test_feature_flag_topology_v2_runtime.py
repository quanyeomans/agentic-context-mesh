"""pytest-bdd test module for feature_flag_topology_v2_runtime.feature.

Pairs the OFF + ON scenarios with their step implementations under
:mod:`tests.bdd.steps.feature_flag_topology_v2_runtime_steps` (registered
in ``tests/conftest.py:pytest_plugins``).
"""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "feature_flag_topology_v2_runtime.feature")

pytestmark = pytest.mark.bdd


@scenario(FEATURE, "flag default-off keeps the single-collection writer dispatch active")
def test_flag_off_legacy_writer() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "flag effective-true routes chunk writes through CollectionRouter")
def test_flag_on_collection_router() -> None:
    """Body populated by @scenario from the .feature file."""
