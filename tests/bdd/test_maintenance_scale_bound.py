"""pytest-bdd binding for maintenance_scale_bound.feature (F63 reference).

Steps live in :mod:`tests.bdd.steps.maintenance_scale_bound_steps`.

The scenarios exercise the real :class:`kairix.core.maintenance.scheduler.MaintenanceScheduler`
with a configurable per-tick cap and assert the scan stays bounded
at production scale. Per F46 the steps reach the scheduler through
constructor injection — no monkeypatch.
"""

from pathlib import Path

import pytest
from pytest_bdd import scenarios

FEATURE = str(Path(__file__).parent / "features" / "maintenance_scale_bound.feature")

pytestmark = pytest.mark.bdd

scenarios(FEATURE)
