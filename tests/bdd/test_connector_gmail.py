"""pytest-bdd test module for connector_gmail.feature.

Pairs the happy-path scenario with the step implementations under
:mod:`tests.bdd.steps.connector_gmail_steps` (registered in
``tests/conftest.py:pytest_plugins``).
"""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "connector_gmail.feature")

pytestmark = pytest.mark.bdd


@scenario(
    FEATURE,
    "A new message in the mailbox surfaces as a created change event",
)
def test_new_message_surfaces_as_created_event() -> None:
    """Body populated by @scenario from the .feature file."""
