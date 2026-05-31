"""pytest-bdd test module for feature_flag_agent_query_queue.feature."""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "feature_flag_agent_query_queue.feature")

pytestmark = pytest.mark.bdd


@scenario(
    FEATURE,
    "Flag OFF — tool_search runs synchronously and no row is written",
)
def test_flag_off_no_row_written() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(
    FEATURE,
    "Flag ON — tool_search runs through the queue and records the row",
)
def test_flag_on_records_delivered_row() -> None:
    """Body populated by @scenario from the .feature file."""
