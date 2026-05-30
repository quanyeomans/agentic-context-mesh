"""pytest-bdd binding for ``chunker_docx_heading.feature`` (ADR-028 Wave G.1).

Steps live in :mod:`tests.bdd.steps.chunker_docx_heading_steps`.
"""

from pathlib import Path

import pytest
from pytest_bdd import scenarios

FEATURE = str(Path(__file__).parent / "features" / "chunker_docx_heading.feature")

pytestmark = pytest.mark.bdd

scenarios(FEATURE)
