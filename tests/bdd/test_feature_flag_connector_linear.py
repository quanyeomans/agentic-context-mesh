"""pytest-bdd binding for feature_flag_connector_linear.feature.

Steps live in :mod:`tests.bdd.steps.feature_flag_connector_linear_steps`.

Both branches drive the production
:func:`kairix.worker.dispatch_linear_sync` with the flag pinned via
:class:`tests.fakes.FakeFeatureFlagResolver`. No env-var manipulation,
no @patch — F1/F2 clean.
"""

from pathlib import Path

import pytest
from pytest_bdd import scenarios

FEATURE = str(Path(__file__).parent / "features" / "feature_flag_connector_linear.feature")

pytestmark = pytest.mark.bdd

scenarios(FEATURE)
