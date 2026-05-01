"""pytest-bdd test module for onboard_check.feature."""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "onboard_check.feature")


@pytest.mark.bdd
@scenario(FEATURE, "All checks pass on a configured instance")
def test_all_checks_pass():
    pass


@pytest.mark.bdd
@scenario(FEATURE, "Missing credentials are detected")
def test_missing_credentials():
    pass
