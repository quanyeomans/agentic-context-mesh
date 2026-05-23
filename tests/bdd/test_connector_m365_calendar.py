"""pytest-bdd test module for connector_m365_calendar.feature.

Pairs the happy-path + delta + cancelled scenarios with their step
implementations under
:mod:`tests.bdd.steps.connector_m365_calendar_steps` (registered in
``tests/conftest.py:pytest_plugins``).
"""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "connector_m365_calendar.feature")

pytestmark = pytest.mark.bdd


@scenario(FEATURE, "First sync without a cursor surfaces a date window of events as created")
def test_first_sync_no_cursor() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "Subsequent sync with a delta cursor surfaces only new changes")
def test_delta_sync_with_cursor() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "A cancelled event surfaces as a deleted change event")
def test_cancelled_event_surfaces_deleted() -> None:
    """Body populated by @scenario from the .feature file."""
