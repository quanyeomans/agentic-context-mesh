"""pytest-bdd binding for connector_notion.feature (Wave E Notion).

Steps live in :mod:`tests.bdd.steps.connector_notion_steps`.

The scenarios exercise the real
:class:`kairix.connectors.notion.NotionConnector` against an
:class:`httpx.MockTransport`-backed Notion REST stub — no real network
call, no monkey-patching, no internal-substitution fakes.
"""

from pathlib import Path

import pytest
from pytest_bdd import scenarios

FEATURE = str(Path(__file__).parent / "features" / "connector_notion.feature")

pytestmark = pytest.mark.bdd

scenarios(FEATURE)
