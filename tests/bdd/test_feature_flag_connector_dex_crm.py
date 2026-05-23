"""pytest-bdd test module for feature_flag_connector_dex_crm.feature.

Pairs the OFF + ON happy-path scenarios with their step implementations
under :mod:`tests.bdd.steps.feature_flag_connector_dex_crm_steps`
(registered in ``tests/conftest.py:pytest_plugins``).
"""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "feature_flag_connector_dex_crm.feature")

pytestmark = pytest.mark.bdd


@scenario(FEATURE, "Flag OFF — the Dex CRM connector does not poll the Dex API")
def test_flag_off_dex_skipped() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "Flag ON — the Dex CRM connector polls the Dex API")
def test_flag_on_dex_polls() -> None:
    """Body populated by @scenario from the .feature file."""
