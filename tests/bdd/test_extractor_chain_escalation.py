"""pytest-bdd binding for ``extractor_chain_escalation.feature``.

Steps live in :mod:`tests.bdd.steps.extractor_chain_escalation_steps`.
"""

from pathlib import Path

import pytest
from pytest_bdd import scenarios

FEATURE = str(Path(__file__).parent / "features" / "extractor_chain_escalation.feature")

pytestmark = pytest.mark.bdd

scenarios(FEATURE)
