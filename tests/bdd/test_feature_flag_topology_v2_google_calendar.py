"""pytest-bdd test module for feature_flag_topology_v2_google_calendar.feature.

Pairs the OFF + ON scenarios with their step implementations under
:mod:`tests.bdd.steps.feature_flag_topology_v2_google_calendar_steps`
(registered in ``tests/conftest.py:pytest_plugins``).
"""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "feature_flag_topology_v2_google_calendar.feature")

pytestmark = pytest.mark.bdd


@scenario(FEATURE, "Flag OFF keeps the Google Calendar connector inert")
def test_flag_off_keeps_connector_inert() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "Flag ON routes the dispatcher through the standard connector pipeline")
def test_flag_on_routes_through_pipeline() -> None:
    """Body populated by @scenario from the .feature file."""
