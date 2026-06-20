"""pytest-bdd binding for connector_skills.feature (skills connector).

Steps live in :mod:`tests.bdd.steps.connector_skills_steps`.

The scenarios exercise the real
:class:`kairix.connectors.skills.SkillsConnector` against a
``tmp_path``-rooted fake ``.claude`` tree (no real ``~/.claude`` read, no
monkey-patching, no internal-substitution fakes — F32).
"""

from pathlib import Path

import pytest
from pytest_bdd import scenarios

FEATURE = str(Path(__file__).parent / "features" / "connector_skills.feature")

pytestmark = pytest.mark.bdd

scenarios(FEATURE)
