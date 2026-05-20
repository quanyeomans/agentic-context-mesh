"""pytest-bdd test module for mcp_ingest_chat.feature.

Step definitions live in ``tests/bdd/steps/mcp_ingest_chat_steps.py``
and are registered via ``pytest_plugins`` in the root ``conftest.py``.
"""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "mcp_ingest_chat.feature")

pytestmark = pytest.mark.bdd


@scenario(FEATURE, "Happy path — agent ingests its conversation into the assigned namespace")
def test_happy_path_agent_ingests() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "Cross-engagement namespace is rejected")
def test_cross_engagement_namespace_rejected() -> None:
    """Body populated by @scenario from the .feature file."""
