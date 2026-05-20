"""pytest-bdd test module for ingest_chat.feature.

Step definitions live in ``tests/bdd/steps/ingest_chat_steps.py`` and
are registered via ``pytest_plugins`` in the root ``conftest.py``.
"""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "ingest_chat.feature")

pytestmark = pytest.mark.bdd


@scenario(FEATURE, "Happy path — 3 conversations write 3 markdown files")
def test_happy_path_writes_markdown_files() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "No-extract mode skips fact persistence")
def test_no_extract_skips_facts() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "Idempotent re-ingest does not duplicate writes")
def test_idempotent_re_ingest() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "Facts are persisted when extract mode is enabled")
def test_facts_persisted_when_extract_enabled() -> None:
    """Body populated by @scenario from the .feature file."""
