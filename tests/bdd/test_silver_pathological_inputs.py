"""pytest-bdd binding for silver_pathological_inputs.feature."""

from pathlib import Path

import pytest
from pytest_bdd import scenarios

FEATURE = str(Path(__file__).parent / "features" / "silver_pathological_inputs.feature")

pytestmark = pytest.mark.bdd

scenarios(FEATURE)
