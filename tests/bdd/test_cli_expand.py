"""pytest-bdd binder for cli_expand.feature.

Steps live in :mod:`tests.bdd.steps.expand_cli_steps`. Every scenario
composes through the public CLI surface (``kairix.use_cases.expand.main``)
with deps injected through the public seam — no direct pipeline
construction, no monkeypatching (F1), no env vars (F2).
"""

from pathlib import Path

import pytest
from pytest_bdd import scenarios

FEATURE = str(Path(__file__).parent / "features" / "cli_expand.feature")

pytestmark = pytest.mark.bdd

scenarios(FEATURE)
