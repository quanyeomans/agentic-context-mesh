"""pytest-bdd test module for setup_wizard.feature."""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "setup_wizard.feature")

pytestmark = pytest.mark.bdd


@scenario(FEATURE, "Operator completes the full setup journey")
def test_full_setup_journey() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "A rejected provider key shows guidance instead of jargon")
def test_rejected_key_shows_guidance() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "An Azure operator validates their key with a deployment name")
def test_azure_deployment_name_happy_path() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "A read-only config file does not strand the operator")
def test_read_only_config_save_rescue() -> None:
    """Body populated by @scenario from the .feature file."""
