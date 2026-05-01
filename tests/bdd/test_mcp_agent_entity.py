"""pytest-bdd test module for mcp_agent_entity.feature."""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "mcp_agent_entity.feature")


@pytest.mark.bdd
@scenario(FEATURE, "Known entity returns complete card")
def test_known_entity_complete_card():
    pass


@pytest.mark.bdd
@scenario(FEATURE, "Unknown entity returns structured not-found")
def test_unknown_entity_not_found():
    pass


@pytest.mark.bdd
@scenario(FEATURE, "Entity lookup never raises")
def test_entity_never_raises():
    pass
