"""pytest-bdd binding for connector_per_tick_budget.feature (ADR-020 / F66).

Steps live in :mod:`tests.bdd.steps.per_tick_budget_steps`. The
scenarios exercise the real
:class:`kairix.core.connectors.pipeline.ConnectorPipeline` through the
F46-sanctioned ``kairix.core.factory.build_connector_pipeline`` entry
point with a scripted :class:`tests.fakes.FakeSourceConnector` that
carries the per-tick budget + watermark attributes declared by ADR-020.
"""

from pathlib import Path

import pytest
from pytest_bdd import scenarios

FEATURE = str(Path(__file__).parent / "features" / "connector_per_tick_budget.feature")

pytestmark = pytest.mark.bdd

scenarios(FEATURE)
