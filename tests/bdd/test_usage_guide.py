"""pytest-bdd test module for usage_guide.feature."""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "usage_guide.feature")

pytestmark = pytest.mark.bdd


@scenario(FEATURE, "Empty topic returns the full guide")
def test_empty_topic_returns_full_guide():
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "Known topic returns a focused slice")
def test_known_topic_returns_focused_slice():
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "Unknown topic returns the fallback orientation slice")
def test_unknown_topic_returns_fallback_orientation_slice():
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "A default install returns the guide with no operator action")
def test_default_install_returns_guide_no_operator_action():
    """Body populated by @scenario from the .feature file (#466)."""


@scenario(FEATURE, "The bundled guide foregrounds the read and write loops")
def test_bundled_guide_foregrounds_read_and_write_loops():
    """Body populated by @scenario from the .feature file (PLA-299)."""
