"""pytest-bdd binding for connector_sharepoint.feature (Wave 5 SharePoint).

Steps live in :mod:`tests.bdd.steps.connector_sharepoint_steps`.

The scenarios exercise the real
:class:`kairix.connectors.sharepoint.SharePointConnector` against an
:class:`httpx.MockTransport`-backed Graph stub — no real network call,
no monkey-patching, no internal-substitution fakes.
"""

from pathlib import Path

import pytest
from pytest_bdd import scenarios

FEATURE = str(Path(__file__).parent / "features" / "connector_sharepoint.feature")

pytestmark = pytest.mark.bdd

scenarios(FEATURE)
