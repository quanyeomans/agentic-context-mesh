"""pytest-bdd binding for cli_slo.feature (PLA-256)."""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "cli_slo.feature")

pytestmark = pytest.mark.bdd


@scenario(FEATURE, "An engineer runs the harness and sees all three SLO dimensions")
def test_harness_reports_all_three_dimensions() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "The harness reports latency at single and high concurrency")
def test_harness_reports_single_and_high_concurrency() -> None:
    """Body populated by @scenario from the .feature file."""
