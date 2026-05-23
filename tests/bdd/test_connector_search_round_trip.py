"""pytest-bdd test module for connector_search_round_trip.feature.

Pins the IM-6 cutover regression: connector-ingested chunks must be
findable via BM25 (and by extension hybrid) search. Steps in
``tests.bdd.steps.connector_search_round_trip_steps`` (registered in
``tests/conftest.py:pytest_plugins``).
"""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "connector_search_round_trip.feature")

pytestmark = pytest.mark.bdd


@scenario(FEATURE, "a chunk written by the connector framework is findable by BM25")
def test_chunk_findable_by_bm25() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "every active document the connector wrote has a corresponding FTS row")
def test_fts_one_to_one_invariant() -> None:
    """Body populated by @scenario from the .feature file."""
