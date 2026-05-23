"""pytest-bdd test module for feature_flag_topology_v2_protocol.feature.

Pairs the OFF + ON scenarios with their step implementations under
:mod:`tests.bdd.steps.feature_flag_topology_v2_protocol_steps` (registered
in ``tests/conftest.py:pytest_plugins``).
"""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "feature_flag_topology_v2_protocol.feature")

pytestmark = pytest.mark.bdd


@scenario(FEATURE, "flag default-off keeps the legacy single-cursor dispatch path active")
def test_flag_off_legacy_path() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "flag effective-true unlocks the capability-Protocol dispatch path")
def test_flag_on_capability_path() -> None:
    """Body populated by @scenario from the .feature file."""
