"""pytest-bdd binding for connector_cursor_persistence.feature (F62 reference).

Steps live in :mod:`tests.bdd.steps.connector_cursor_persistence_steps`.

The scenarios exercise the real :class:`kairix.core.connectors.pipeline.ConnectorPipeline`
through its documented DI seams (constructor injection of stores +
fakes) — no monkeypatch, no @patch. The scripted
:class:`tests.fakes.FakeSourceConnector` simulates both opaque-token
and ISO-timestamp cursor shapes per the F62 spec.
"""

from pathlib import Path

import pytest
from pytest_bdd import scenarios

FEATURE = str(Path(__file__).parent / "features" / "connector_cursor_persistence.feature")

pytestmark = pytest.mark.bdd

scenarios(FEATURE)
