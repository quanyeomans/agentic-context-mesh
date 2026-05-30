"""pytest-bdd test module for feature_flag_connector_gmail.feature.

Pairs the OFF + ON scenarios with the step implementations under
:mod:`tests.bdd.steps.feature_flag_connector_gmail_steps` (registered
in ``tests/conftest.py:pytest_plugins``).
"""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "feature_flag_connector_gmail.feature")

pytestmark = pytest.mark.bdd


@scenario(
    FEATURE,
    "Flag OFF — the Gmail connector slot is a no-op",
)
def test_flag_off_gmail_connector_noop() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(
    FEATURE,
    "Flag ON — the Gmail connector pipeline runs",
)
def test_flag_on_gmail_connector_runs() -> None:
    """Body populated by @scenario from the .feature file."""
