"""pytest-bdd test module for cli_maintenance.feature."""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "cli_maintenance.feature")

pytestmark = pytest.mark.bdd


@scenario(FEATURE, "Fresh database runs ANALYZE on warm-up")
def test_fresh_db_runs_analyze() -> None:
    """Scenario body populated by @scenario from the .feature file."""


@scenario(FEATURE, "Database with recent stats skips ANALYZE on warm-up")
def test_recent_stats_skip_analyze() -> None:
    """Scenario body populated by @scenario from the .feature file."""


@scenario(FEATURE, "Operator runs kairix maintenance analyze")
def test_operator_runs_maintenance_analyze() -> None:
    """Scenario body populated by @scenario from the .feature file."""
