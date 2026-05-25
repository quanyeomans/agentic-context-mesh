"""pytest-bdd binding for connector_bronze.feature.

Drives the real :class:`kairix.core.connectors.bronze.FilesystemBronzeStore`
against a ``tmp_path`` bronze root. Steps live in
:mod:`tests.bdd.steps.connector_bronze_steps`.
"""

from pathlib import Path

import pytest
from pytest_bdd import scenarios

FEATURE = str(Path(__file__).parent / "features" / "connector_bronze.feature")

pytestmark = pytest.mark.bdd

scenarios(FEATURE)
