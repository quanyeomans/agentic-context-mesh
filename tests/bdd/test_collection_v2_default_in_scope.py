"""pytest-bdd binding for collection_v2_default_in_scope.feature."""

from pathlib import Path

import pytest
from pytest_bdd import scenarios

# Step definitions live alongside in
# steps/collection_v2_default_in_scope_steps.py — pytest-bdd auto-discovers
# them at collection time.
from tests.bdd.steps import collection_v2_default_in_scope_steps  # noqa: F401

FEATURE = str(Path(__file__).parent / "features" / "collection_v2_default_in_scope.feature")

pytestmark = pytest.mark.bdd

scenarios(FEATURE)
