"""pytest-bdd binding for ``extractor_pdf_fallback.feature`` (MM-1).

Steps live in :mod:`tests.bdd.steps.extractor_pdf_fallback_steps`.
"""

from pathlib import Path

import pytest
from pytest_bdd import scenarios

FEATURE = str(Path(__file__).parent / "features" / "extractor_pdf_fallback.feature")

pytestmark = pytest.mark.bdd

scenarios(FEATURE)
