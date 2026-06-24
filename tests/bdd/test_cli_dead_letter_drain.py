"""pytest-bdd test module for cli_dead_letter_drain.feature (PR-5)."""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "cli_dead_letter_drain.feature")

pytestmark = pytest.mark.bdd


@scenario(FEATURE, "Drain one orphaned source — its permanently-unprocessable row clears")
def test_drain_one_orphaned_source() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "Drain every source — all distinct sources are swept")
def test_drain_every_source() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "Dry-run reports what would drain without mutating")
def test_drain_dry_run() -> None:
    """Body populated by @scenario from the .feature file."""
