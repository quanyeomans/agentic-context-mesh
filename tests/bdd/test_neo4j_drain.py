"""pytest-bdd binding for neo4j_drain.feature (GH #334).

Steps live in :mod:`tests.bdd.steps.neo4j_drain_steps` and are
registered as a pytest plugin from ``tests/conftest.py`` so pytest-bdd
can resolve them. The scenarios drive the production
:func:`kairix.core.curator.drain.run_neo4j_drain_tick` via the F47
sanctioned factory entry point
:func:`kairix.core.factory.build_neo4j_drainer`.
"""

from pathlib import Path

import pytest
from pytest_bdd import scenarios

FEATURE = str(Path(__file__).parent / "features" / "neo4j_drain.feature")

pytestmark = pytest.mark.bdd

scenarios(FEATURE)
