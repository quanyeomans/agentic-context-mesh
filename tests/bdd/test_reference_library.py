"""pytest-bdd test module for reference_library.feature."""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "reference_library.feature")


@pytest.mark.bdd
@scenario(FEATURE, "Search finds engineering documents")
def test_search_engineering():
    pass


@pytest.mark.bdd
@scenario(FEATURE, "Search finds philosophy documents")
def test_search_philosophy():
    pass


@pytest.mark.bdd
@scenario(FEATURE, "Search results have no frontmatter in snippets")
def test_no_frontmatter_in_snippets():
    pass


@pytest.mark.bdd
@scenario(FEATURE, "BM25 and vector search both contribute")
def test_bm25_and_vector():
    pass
