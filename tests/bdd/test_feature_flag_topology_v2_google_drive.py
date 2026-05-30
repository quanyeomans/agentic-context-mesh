"""pytest-bdd binding for feature_flag_topology_v2_google_drive.feature.

Steps live in :mod:`tests.bdd.steps.feature_flag_topology_v2_google_drive_steps`.

Both branches drive the real
:class:`kairix.connectors.google_drive.GoogleDriveConnector` with the
flag pinned via :class:`tests.fakes.FakeFeatureFlagResolver`. No
env-var manipulation, no @patch — F1/F2 clean.
"""

from pathlib import Path

import pytest
from pytest_bdd import scenarios

FEATURE = str(Path(__file__).parent / "features" / "feature_flag_topology_v2_google_drive.feature")

pytestmark = pytest.mark.bdd

scenarios(FEATURE)
