"""pytest-bdd test module for feature_flag_topology_v2_dex_crm.feature.

Pairs the OFF + ON scenarios with their step implementations under
:mod:`tests.bdd.steps.feature_flag_topology_v2_dex_crm_steps` (registered
in ``tests/conftest.py:pytest_plugins``).
"""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "feature_flag_topology_v2_dex_crm.feature")

pytestmark = pytest.mark.bdd


@scenario(
    FEATURE,
    "Flag OFF keeps the legacy single-cursor list_changes path and a single dex root FOLDER node",
)
def test_flag_off_legacy_single_cursor_shape() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(
    FEATURE,
    "Flag ON emits one tenant Container and walks the dex hierarchy parent-before-child",
)
def test_flag_on_tenant_container_shape() -> None:
    """Body populated by @scenario from the .feature file."""
