"""pytest-bdd binding for sharepoint_rate_limit.feature.

Steps live in :mod:`tests.bdd.steps.sharepoint_rate_limit_steps`.

Drives the real :class:`kairix.connectors.sharepoint.SharePointConnector`
against an :class:`httpx.MockTransport`-backed Graph stub that returns
a single 429 + ``Retry-After`` before recovering. The recorded sleep
budget proves the client honoured the server header — no
monkey-patching, no internal-substitution fakes, no wall-clock delay.
"""

from pathlib import Path

import pytest
from pytest_bdd import scenarios

FEATURE = str(Path(__file__).parent / "features" / "sharepoint_rate_limit.feature")

pytestmark = pytest.mark.bdd

scenarios(FEATURE)
