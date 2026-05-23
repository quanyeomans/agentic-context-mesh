"""pytest-bdd test module for worker_preflight.feature.

Three scenarios, all carrying the ``bdd`` marker via the parent
package's ``pytestmark`` discipline:

  - clean DB → healthy
  - DB missing FTS rows for active documents → unhealthy
  - auto-heal recovers the FTS index and the second audit passes
"""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "worker_preflight.feature")

pytestmark = pytest.mark.bdd


@scenario(FEATURE, "Preflight passes on a clean database")
def test_preflight_clean_db_passes() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "Preflight surfaces documents-without-fts gap")
def test_preflight_surfaces_fts_gap() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "Auto-heal rebuilds the FTS index when documents-without-fts is present")
def test_preflight_auto_heal_rebuilds_fts() -> None:
    """Body populated by @scenario from the .feature file."""
