"""pytest-bdd test module for feature_flag_topology_v2_gmail.feature.

Pairs the OFF + ON scenarios with the step implementations under
:mod:`tests.bdd.steps.feature_flag_topology_v2_gmail_steps` (registered
in ``tests/conftest.py:pytest_plugins``).
"""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "feature_flag_topology_v2_gmail.feature")

pytestmark = pytest.mark.bdd


@scenario(
    FEATURE,
    "Flag OFF keeps the legacy single-cursor list_changes path",
)
def test_flag_off_legacy_single_cursor() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(
    FEATURE,
    "Flag ON drains the History API against the container cursor",
)
def test_flag_on_per_container_cursor() -> None:
    """Body populated by @scenario from the .feature file."""
