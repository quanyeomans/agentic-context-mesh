"""pytest-bdd binding for connector_obsidian.feature (IM-5 Wave 2).

Steps live in :mod:`tests.bdd.steps.connector_obsidian_steps`.

The scenarios exercise the real :class:`kairix.connectors.obsidian.ObsidianConnector`
against a ``tmp_path`` vault — no monkey-patching, no internal-substitution
fakes. The watchdog observer is started for the happy_path scenario and
stopped at scenario teardown via the connector's context-manager protocol;
the reconciliation scenarios drive the full-scan path directly so they
don't depend on watchdog timing.
"""

from pathlib import Path

import pytest
from pytest_bdd import scenarios

FEATURE = str(Path(__file__).parent / "features" / "connector_obsidian.feature")

pytestmark = pytest.mark.bdd

scenarios(FEATURE)
