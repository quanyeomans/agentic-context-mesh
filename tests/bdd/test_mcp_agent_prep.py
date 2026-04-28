"""BDD test runner for MCP agent prep tool scenarios."""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "mcp_agent_prep.feature")


@pytest.mark.bdd
@scenario(FEATURE, "L0 prep returns a short summary with sources")
def test_l0_prep_returns_summary():
    pass


@pytest.mark.bdd
@scenario(FEATURE, "Prep with no matching documents returns informative message")
def test_prep_no_docs_returns_message():
    pass


@pytest.mark.bdd
@scenario(FEATURE, "Prep tool never raises to the caller")
def test_prep_never_raises():
    pass
