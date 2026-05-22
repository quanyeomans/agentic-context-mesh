"""pytest-bdd binding for ``extractor_pptx.feature`` (OF-1).

Steps live in :mod:`tests.bdd.steps.extractor_pptx_steps`.
"""

from pathlib import Path

import pytest
from pytest_bdd import scenarios

FEATURE = str(Path(__file__).parent / "features" / "extractor_pptx.feature")

pytestmark = pytest.mark.bdd

scenarios(FEATURE)
