"""pytest-bdd binding for feature_flag_connector_dex_crm.feature.

Steps live in :mod:`tests.bdd.steps.feature_flag_connector_dex_crm_steps`
(registered in ``tests/conftest.py:pytest_plugins``).

Both branches drive the production
:func:`kairix.worker.run_connector_sync_pipeline` with the flag pinned
via :class:`tests.fakes.FakeFeatureFlagResolver`; the enablement gate
under test is :func:`kairix.worker.connector_enabled`. No env-var
manipulation, no @patch — F1/F2 clean.
"""

from pathlib import Path

import pytest
from pytest_bdd import scenarios

FEATURE = str(Path(__file__).parent / "features" / "feature_flag_connector_dex_crm.feature")

pytestmark = pytest.mark.bdd

scenarios(FEATURE)
