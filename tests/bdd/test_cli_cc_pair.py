"""pytest-bdd test module for cli_cc_pair.feature.

Pairs the 5 scenarios with the step implementations in
``tests/bdd/steps/cli_cc_pair_steps.py`` (registered in
``tests/conftest.py:pytest_plugins``).
"""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "cli_cc_pair.feature")

pytestmark = pytest.mark.bdd


@scenario(FEATURE, "cc-pair list reports the friendly empty-state line when nothing is declared")
def test_cc_pair_list_empty() -> None:
    """Body populated by @scenario."""


@scenario(FEATURE, "cc-pair create inserts a fresh row at status SCHEDULED")
def test_cc_pair_create_scheduled() -> None:
    """Body populated by @scenario."""


@scenario(FEATURE, "cc-pair pause rejects an illegal transition with an operator-friendly message")
def test_cc_pair_pause_illegal() -> None:
    """Body populated by @scenario."""


@scenario(FEATURE, "cc-pair resume from PAUSED transitions back to ACTIVE")
def test_cc_pair_resume_active() -> None:
    """Body populated by @scenario."""


@scenario(FEATURE, "cc-pair delete transitions to DELETING")
def test_cc_pair_delete() -> None:
    """Body populated by @scenario."""
