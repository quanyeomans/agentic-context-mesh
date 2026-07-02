"""pytest-bdd binder for feature_flag_recommender.feature (F54 both-branch).

Steps live in :mod:`tests.bdd.steps.feature_flag_recommender_steps`. Both
branches drive the production adapter (``tool_recommend_capabilities``) and the worker
boot hook (``maybe_build_capability_corpus_at_boot``) with the flag pinned
via :class:`tests.fakes.FakeFeatureFlagResolver`. No env-var manipulation,
no @patch — F1/F2 clean.
"""

from pathlib import Path

import pytest
from pytest_bdd import scenarios

FEATURE = str(Path(__file__).parent / "features" / "feature_flag_recommender.feature")

pytestmark = pytest.mark.bdd

scenarios(FEATURE)
