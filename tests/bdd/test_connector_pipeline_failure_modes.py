"""pytest-bdd binding for connector_pipeline_failure_modes.feature.

Steps live in :mod:`tests.bdd.steps.connector_pipeline_failure_modes_steps`.
"""

from pathlib import Path

import pytest
from pytest_bdd import scenarios

FEATURE = str(Path(__file__).parent / "features" / "connector_pipeline_failure_modes.feature")

pytestmark = pytest.mark.bdd

scenarios(FEATURE)
