"""pytest-bdd test module for eval_ci_gates.feature.

Step definitions live in ``tests/bdd/steps/eval_ci_gates_steps.py`` and
are registered via ``pytest_plugins`` in the root ``conftest.py``.

Plan B-parity Week 4 Stream A — pins the CI workflow extensions and the
baseline-file layout so accidental removal trips the gate.
"""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "eval_ci_gates.feature")

pytestmark = pytest.mark.bdd


@scenario(FEATURE, "Conversation-eval gate parses as valid GitHub Actions")
def test_conversation_eval_gate_parses() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "LoCoMo nightly workflow parses as valid GitHub Actions")
def test_locomo_nightly_parses() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "Every engagement-* corpus has a pinned baseline file")
def test_every_corpus_has_baseline() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "Workflow files introduce no CI silencers")
def test_workflows_have_no_silencers() -> None:
    """Body populated by @scenario from the .feature file."""
