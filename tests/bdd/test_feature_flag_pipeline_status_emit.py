"""pytest-bdd binding for feature_flag_pipeline_status_emit.feature.

Pairs the OFF + ON scenarios with the step implementations in
``tests.bdd.steps.feature_flag_pipeline_status_emit_steps`` (registered
in ``tests/conftest.py:pytest_plugins``).
"""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "feature_flag_pipeline_status_emit.feature")

pytestmark = pytest.mark.bdd


@scenario(FEATURE, "flag OFF leaves pipeline_item_status untouched")
def test_flag_off_no_writes() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "flag ON appends the emit row")
def test_flag_on_appends() -> None:
    """Body populated by @scenario from the .feature file."""
