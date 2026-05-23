"""pytest-bdd binding for connector_dex_crm.feature (Wave 5 KP-1).

Steps live in :mod:`tests.bdd.steps.connector_dex_crm_steps`.

The scenarios exercise the real :class:`kairix.connectors.dex_crm.DexCrmConnector`
through its documented DI seams — a recording :class:`httpx.MockTransport`
keeps every assertion local (no real Dex API call) and a scripted
:class:`ApiKeyAuth` subclass either yields a bearer or raises
:class:`MissingCredentialsError` depending on the scenario.
"""

from pathlib import Path

import pytest
from pytest_bdd import scenarios

FEATURE = str(Path(__file__).parent / "features" / "connector_dex_crm.feature")

pytestmark = pytest.mark.bdd

scenarios(FEATURE)
