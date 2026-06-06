"""pytest-bdd test module for agent_scope_callsites.feature (PR 1.2 / #420)."""

from pathlib import Path

import pytest
from pytest_bdd import scenarios

# Step definitions live in steps/agent_scope_callsites_steps.py — pytest-bdd
# auto-discovers them at collection time.
from tests.bdd.steps import agent_scope_callsites_steps  # noqa: F401

FEATURE = str(Path(__file__).parent / "features" / "agent_scope_callsites.feature")

pytestmark = pytest.mark.bdd

scenarios(FEATURE)
