"""pytest-bdd binding for ``extractor_passthrough.feature`` (IM-4).

Steps live in :mod:`tests.bdd.steps.extractor_passthrough_steps`.
"""

from pathlib import Path

import pytest
from pytest_bdd import scenarios

FEATURE = str(Path(__file__).parent / "features" / "extractor_passthrough.feature")

pytestmark = pytest.mark.bdd

scenarios(FEATURE)
