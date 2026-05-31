"""pytest-bdd test module for embedding_cache.feature."""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "embedding_cache.feature")


@pytest.mark.bdd
@scenario(FEATURE, "A vector written via the cache comes back equal")
def test_roundtrip() -> None:
    """Body populated by @scenario from the .feature file."""


@pytest.mark.bdd
@scenario(FEATURE, "A repeat embed run hits the cache and skips the provider")
def test_repeat_run_zero_provider_calls() -> None:
    """Body populated by @scenario from the .feature file."""


@pytest.mark.bdd
@scenario(FEATURE, "A partial cache forwards only the misses to the provider")
def test_partial_cache_dispatches_misses() -> None:
    """Body populated by @scenario from the .feature file."""


@pytest.mark.bdd
@scenario(FEATURE, "Switching the model leaves the previous model's cache slice untouched")
def test_model_swap_isolates() -> None:
    """Body populated by @scenario from the .feature file."""
