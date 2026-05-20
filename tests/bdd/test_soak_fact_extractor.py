"""pytest-bdd binding for soak_fact_extractor.feature (Plan B-parity Stream C).

The bound scenarios are gated at the step level on ``KAIRIX_SOAK=1`` —
collected always (so the F12 happy-path requirement is satisfied), skipped
at runtime with rationale when the env var is unset. The nightly soak
workflow (Stream A) sets the env var; the normal Stage 2 BDD job collects
+ skips. See ``tests/bdd/steps/soak_fact_extractor_steps.py``.
"""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "soak_fact_extractor.feature")

# F8: every test_* carries a category marker (``bdd``); ``soak`` is the
# secondary tag the nightly workflow filters on.
pytestmark = [pytest.mark.bdd, pytest.mark.soak]


@scenario(FEATURE, "Continuous-ingest soak keeps latency and memory bounded")
def test_continuous_ingest_soak() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "Concurrent ingest and query meet read-your-writes consistency")
def test_concurrent_ingest_and_query() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "Large fact store keeps federated search and conflict lookup fast")
def test_large_fact_store_search_budgets() -> None:
    """Body populated by @scenario from the .feature file."""
