"""pytest-bdd binding for onboard_scan_discovers_agents.feature."""

from __future__ import annotations

import pytest
from pytest_bdd import scenario


@pytest.mark.bdd
@scenario(
    "features/onboard_scan_discovers_agents.feature",
    "scan finds two agents with mixed harnesses and emits a YAML block",
)
def test_onboard_scan_yaml() -> None:
    """Body populated by @scenario from the .feature file."""


@pytest.mark.bdd
@scenario(
    "features/onboard_scan_discovers_agents.feature",
    "onboard agent for unknown agent surfaces an actionable error",
)
def test_onboard_agent_unknown() -> None:
    """Body populated by @scenario from the .feature file."""
