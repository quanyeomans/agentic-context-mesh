"""pytest-bdd binding for ``extractor_xlsx.feature`` (OF-3).

Steps live in :mod:`tests.bdd.steps.extractor_xlsx_steps`.
"""

from pathlib import Path

import pytest
from pytest_bdd import scenarios

FEATURE = str(Path(__file__).parent / "features" / "extractor_xlsx.feature")

pytestmark = pytest.mark.bdd

scenarios(FEATURE)
