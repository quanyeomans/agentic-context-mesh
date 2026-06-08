"""pytest-bdd test module for rrf_asymmetric_fusion.feature (Issue #454)."""

from pathlib import Path

import pytest
from pytest_bdd import scenario

# Import the step impls so pytest-bdd discovers them at collection time.
# tests/conftest.py's pytest_plugins entry is the suite-wide source of
# truth; this import documents the coupling at the binding-file level.
import tests.bdd.steps.rrf_asymmetric_fusion_steps  # noqa: F401

FEATURE = str(Path(__file__).parent / "features" / "rrf_asymmetric_fusion.feature")


@pytest.mark.bdd
@scenario(FEATURE, "Symmetric limits behave like classic Cormack 2009 RRF")
def test_symmetric_limits_behave_like_classic_cormack():
    """Body populated by @scenario from the .feature file."""


@pytest.mark.bdd
@scenario(FEATURE, "Asymmetric limits weight the shorter list appropriately")
def test_asymmetric_limits_weight_shorter_list():
    """Body populated by @scenario from the .feature file."""


@pytest.mark.bdd
@scenario(FEATURE, "Document in shorter list only is not penalised by absent tail")
def test_document_in_shorter_list_only_not_penalised():
    """Body populated by @scenario from the .feature file."""
