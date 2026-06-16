"""pytest-bdd test module for benchmark_install_corpus.feature (#450)."""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "benchmark_install_corpus.feature")


@pytest.mark.bdd
@scenario(FEATURE, "Operator installs the corpus and the reflib suite finds it")
def test_operator_installs_corpus_reflib_finds_it():
    """Body populated by @scenario from the .feature file."""


@pytest.mark.bdd
@scenario(FEATURE, "A corrupt download fails closed")
def test_corrupt_download_fails_closed():
    """Body populated by @scenario from the .feature file."""
