"""pytest-bdd binding for cli_doctor.feature (PR 1.5 / #420)."""

from __future__ import annotations

import pytest
from pytest_bdd import scenario


@pytest.mark.bdd
@scenario(
    "features/cli_doctor.feature",
    "every configured agent has populated recent surfaces",
)
def test_doctor_all_green() -> None:
    """Body populated by @scenario from the .feature file."""


@pytest.mark.bdd
@scenario(
    "features/cli_doctor.feature",
    "an agent has a missing surface directory",
)
def test_doctor_all_missing_surface() -> None:
    """Body populated by @scenario from the .feature file."""


@pytest.mark.bdd
@scenario(
    "features/cli_doctor.feature",
    "doctor agent --name returns a single AgentHealth envelope",
)
def test_doctor_single_agent_json() -> None:
    """Body populated by @scenario from the .feature file."""
