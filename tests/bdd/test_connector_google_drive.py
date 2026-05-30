"""pytest-bdd binding for connector_google_drive.feature (Wave E Google Drive).

Steps live in :mod:`tests.bdd.steps.connector_google_drive_steps`.

The scenarios exercise the real
:class:`kairix.connectors.google_drive.GoogleDriveConnector` against
an :class:`httpx.MockTransport`-backed Drive stub — no real network
call, no monkey-patching, no internal-substitution fakes.
"""

from pathlib import Path

import pytest
from pytest_bdd import scenarios

FEATURE = str(Path(__file__).parent / "features" / "connector_google_drive.feature")

pytestmark = pytest.mark.bdd

scenarios(FEATURE)
