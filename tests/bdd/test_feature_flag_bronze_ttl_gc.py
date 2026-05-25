"""pytest-bdd binding for feature_flag_bronze_ttl_gc.feature.

#316 — F54 both-branch coverage for the bronze TTL GC flag.

Step impls live in :mod:`tests.bdd.steps.feature_flag_bronze_ttl_gc_steps`
(registered as a pytest plugin in ``tests/conftest.py``).
"""

from pathlib import Path

import pytest
from pytest_bdd import scenarios

FEATURE = str(Path(__file__).parent / "features" / "feature_flag_bronze_ttl_gc.feature")

pytestmark = pytest.mark.bdd

scenarios(FEATURE)
