"""pytest-bdd binding for connector_linear.feature (Linear connector).

Steps live in :mod:`tests.bdd.steps.connector_linear_steps`.

The scenarios exercise the real
:class:`kairix.connectors.linear.LinearConnector` against a scripted
:class:`tests.fakes.FakeLinearApiClient` (no real network call, no
monkey-patching, no internal-substitution fakes).
"""

from pathlib import Path

import pytest
from pytest_bdd import scenarios

FEATURE = str(Path(__file__).parent / "features" / "connector_linear.feature")

pytestmark = pytest.mark.bdd

scenarios(FEATURE)
