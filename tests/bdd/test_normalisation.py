"""pytest-bdd test module for normalisation.feature."""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "normalisation.feature")


@pytest.mark.bdd
@scenario(FEATURE, "Boilerplate files are filtered")
def test_boilerplate_filtered():
    pass


@pytest.mark.bdd
@scenario(FEATURE, "Frontmatter is injected")
def test_frontmatter_injected():
    pass


@pytest.mark.bdd
@scenario(FEATURE, "CC-BY-SA sources are excluded")
def test_ccbysa_excluded():
    pass
