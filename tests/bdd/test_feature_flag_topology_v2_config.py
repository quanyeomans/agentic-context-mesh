"""pytest-bdd test module for feature_flag_topology_v2_config.feature.

Both OFF + ON scenarios per F54.
"""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "feature_flag_topology_v2_config.feature")

pytestmark = pytest.mark.bdd


@scenario(FEATURE, "flag default-off — kairix features status topology v2 surface is inert")
def test_flag_off_inert_surface() -> None:
    """Body populated by @scenario."""


@scenario(FEATURE, "flag effective-true — topology v2 surface is live + diagnostics surface populated")
def test_flag_on_live_surface() -> None:
    """Body populated by @scenario."""
