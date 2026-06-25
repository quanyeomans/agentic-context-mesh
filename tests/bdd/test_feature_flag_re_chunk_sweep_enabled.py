"""pytest-bdd binding for feature_flag_re_chunk_sweep_enabled.feature.

Pairs the OFF + ON scenarios with the step implementations in
``tests.bdd.steps.feature_flag_re_chunk_sweep_enabled_steps`` (registered in
``tests/conftest.py:pytest_plugins``).
"""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "feature_flag_re_chunk_sweep_enabled.feature")

pytestmark = pytest.mark.bdd


@scenario(FEATURE, "flag OFF skips the re-chunk sweep")
def test_flag_off_skips_sweep() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "flag ON runs the re-chunk sweep")
def test_flag_on_runs_sweep() -> None:
    """Body populated by @scenario from the .feature file."""
