"""pytest-bdd binding for ``extractor_markitdown.feature`` (IM-4).

Steps live in :mod:`tests.bdd.steps.extractor_markitdown_steps`.
"""

from pathlib import Path

import pytest
from pytest_bdd import scenarios

FEATURE = str(Path(__file__).parent / "features" / "extractor_markitdown.feature")

pytestmark = pytest.mark.bdd

scenarios(FEATURE)
