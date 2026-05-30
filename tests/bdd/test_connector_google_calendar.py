"""pytest-bdd test module for connector_google_calendar.feature.

Pairs the happy-path + delta + cancelled + recurrence scenarios with
their step implementations under
:mod:`tests.bdd.steps.connector_google_calendar_steps` (registered in
``tests/conftest.py:pytest_plugins``).
"""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "connector_google_calendar.feature")

pytestmark = pytest.mark.bdd


@scenario(FEATURE, "First sync without a cursor surfaces a window of events as created")
def test_first_sync_no_cursor() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "Subsequent sync with a sync token surfaces only new changes")
def test_delta_sync_with_cursor() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "A cancelled event does not surface as a change event")
def test_cancelled_event_skipped() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "A recurring master event surfaces once with the RRULE captured in metadata")
def test_recurring_master_keeps_rrule() -> None:
    """Body populated by @scenario from the .feature file."""
