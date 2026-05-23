"""pytest-bdd test module for feature_flag_connector_m365_email_headers.feature.

Pairs the OFF + ON happy-path scenarios with their step implementations
under :mod:`tests.bdd.steps.feature_flag_connector_m365_email_headers_steps`
(registered in ``tests/conftest.py:pytest_plugins``).
"""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "feature_flag_connector_m365_email_headers.feature")

pytestmark = pytest.mark.bdd


@scenario(FEATURE, "Flag OFF — the M365 connector slot is a no-op")
def test_flag_off_m365_noop() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "Flag ON — the M365 connector pipeline runs")
def test_flag_on_m365_pipeline() -> None:
    """Body populated by @scenario from the .feature file."""
