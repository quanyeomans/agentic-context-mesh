"""pytest-bdd test module for eval_suite.feature.

Step definitions live in ``tests/bdd/steps/eval_suite_steps.py`` and
are registered via ``pytest_plugins`` in the root ``conftest.py``.
"""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "eval_suite.feature")

pytestmark = pytest.mark.bdd


@scenario(FEATURE, "Happy path — operator sees per-category pass rates")
def test_happy_path_per_category_pass_rates() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "Regression gate detects degraded mean score")
def test_regression_gate_detects_degradation() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "Regression gate passes when run beats the baseline")
def test_regression_gate_passes_on_improvement() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "Missing ground truth file surfaces an actionable error")
def test_missing_ground_truth_actionable_error() -> None:
    """Body populated by @scenario from the .feature file."""
