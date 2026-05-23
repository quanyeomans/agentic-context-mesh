"""pytest-bdd test module for feature_flag_obsidian_connector_primary.feature.

Pairs the OFF + ON happy-path scenarios with their step implementations
under :mod:`tests.bdd.steps.feature_flag_obsidian_connector_primary_steps`
(registered in ``tests/conftest.py:pytest_plugins``).
"""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "feature_flag_obsidian_connector_primary.feature")

pytestmark = pytest.mark.bdd


@scenario(FEATURE, "Flag OFF — the legacy document scanner indexes the document store")
def test_flag_off_legacy_branch() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "Flag ON — the obsidian connector pipeline indexes the document store")
def test_flag_on_connector_branch() -> None:
    """Body populated by @scenario from the .feature file."""
