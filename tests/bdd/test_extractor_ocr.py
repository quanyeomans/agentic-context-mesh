"""pytest-bdd binding for ``extractor_ocr.feature`` (MM-2).

Steps live in :mod:`tests.bdd.steps.extractor_ocr_steps`.
"""

from pathlib import Path

import pytest
from pytest_bdd import scenarios

FEATURE = str(Path(__file__).parent / "features" / "extractor_ocr.feature")

pytestmark = pytest.mark.bdd

scenarios(FEATURE)
