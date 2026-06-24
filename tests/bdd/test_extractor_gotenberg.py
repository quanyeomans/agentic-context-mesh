"""pytest-bdd binding for ``extractor_gotenberg.feature`` (PR-3).

Steps live in :mod:`tests.bdd.steps.extractor_gotenberg_steps`.
"""

from pathlib import Path

import pytest
from pytest_bdd import scenarios

FEATURE = str(Path(__file__).parent / "features" / "extractor_gotenberg.feature")

pytestmark = pytest.mark.bdd

scenarios(FEATURE)
