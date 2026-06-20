"""pytest-bdd binder for cli_recommend.feature.

Steps live in :mod:`tests.bdd.steps.recommend_cli_steps`. Every scenario
composes through the public CLI surface
(``kairix.use_cases.recommend.main``) with deps + flag_reader injected
through the public seams — no direct pipeline construction, no
monkeypatching (F1), no env vars (F2).
"""

from pathlib import Path

import pytest
from pytest_bdd import scenarios

FEATURE = str(Path(__file__).parent / "features" / "cli_recommend.feature")

pytestmark = pytest.mark.bdd

scenarios(FEATURE)
