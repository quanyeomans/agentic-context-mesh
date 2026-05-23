"""pytest-bdd binding for connector_m365_email_headers.feature (KP-2 Wave 5).

Steps live in :mod:`tests.bdd.steps.connector_m365_email_headers_steps`.

The scenarios exercise the real
:class:`kairix.connectors.m365_email_headers.M365EmailHeadersConnector`
against an :class:`httpx.MockTransport`-backed Graph stub — no real
network call, no monkey-patching, no internal-substitution fakes.
"""

from pathlib import Path

import pytest
from pytest_bdd import scenarios

FEATURE = str(Path(__file__).parent / "features" / "connector_m365_email_headers.feature")

pytestmark = pytest.mark.bdd

scenarios(FEATURE)
