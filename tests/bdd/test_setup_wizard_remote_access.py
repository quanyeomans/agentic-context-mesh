"""pytest-bdd test module for setup_wizard_remote_access.feature (#500).

Steps live in :mod:`tests.bdd.steps.setup_wizard_remote_access_steps`.
"""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "setup_wizard_remote_access.feature")

pytestmark = pytest.mark.bdd


@scenario(FEATURE, "A tunnelled browser reaches the wizard with the tokened URL")
def test_tunnelled_browser_reaches_wizard() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "A browser without the tokened URL is blocked")
def test_browser_without_token_blocked() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "The host-shell operator reaches the wizard with no token")
def test_host_shell_loopback_reaches_wizard() -> None:
    """Body populated by @scenario from the .feature file."""
