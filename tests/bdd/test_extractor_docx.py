"""pytest-bdd binding for ``extractor_docx.feature`` (OF-2).

Steps live in :mod:`tests.bdd.steps.extractor_docx_steps`.
"""

from pathlib import Path

import pytest
from pytest_bdd import scenarios

FEATURE = str(Path(__file__).parent / "features" / "extractor_docx.feature")

pytestmark = pytest.mark.bdd

scenarios(FEATURE)
