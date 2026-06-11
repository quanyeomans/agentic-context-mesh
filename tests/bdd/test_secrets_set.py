"""pytest-bdd test module for secrets_set.feature."""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "secrets_set.feature")

pytestmark = pytest.mark.bdd


@scenario(FEATURE, "Storing a credential reports the destination and the next step")
def test_set_happy_path() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "The stored value never appears in the command output")
def test_set_value_never_echoed() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "A non-canonical name is rejected with corrective examples")
def test_set_rejects_non_canonical_name() -> None:
    """Body populated by @scenario from the .feature file."""
