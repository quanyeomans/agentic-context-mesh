"""pytest-bdd binding for ``chunker_sheet_row.feature`` (ADR-028 Wave G.1).

Steps live in :mod:`tests.bdd.steps.chunker_sheet_row_steps`.
"""

from pathlib import Path

import pytest
from pytest_bdd import scenarios

FEATURE = str(Path(__file__).parent / "features" / "chunker_sheet_row.feature")

pytestmark = pytest.mark.bdd

scenarios(FEATURE)
