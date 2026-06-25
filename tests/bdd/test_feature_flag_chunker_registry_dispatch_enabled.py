"""pytest-bdd binding for feature_flag_chunker_registry_dispatch_enabled.feature.

Pairs the OFF + ON scenarios with the step implementations in
``tests.bdd.steps.feature_flag_chunker_registry_dispatch_enabled_steps``
(registered in ``tests/conftest.py:pytest_plugins``).
"""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "feature_flag_chunker_registry_dispatch_enabled.feature")

pytestmark = pytest.mark.bdd


@scenario(FEATURE, "flag OFF keeps the paragraph fallback chunker version")
def test_flag_off_fallback() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "flag ON dispatches to the per-type chunker")
def test_flag_on_dispatch() -> None:
    """Body populated by @scenario from the .feature file."""
