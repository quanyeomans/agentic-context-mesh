"""pytest-bdd test module for agent_query_queue.feature."""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "agent_query_queue.feature")

pytestmark = pytest.mark.bdd


@scenario(
    FEATURE,
    "Slow tool_search returns plain text, then carries result on next call",
)
def test_slow_search_carries_along() -> None:
    """Body populated by @scenario from the .feature file."""
