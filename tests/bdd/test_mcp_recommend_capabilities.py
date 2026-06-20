"""pytest-bdd binder for mcp_recommend_capabilities.feature.

Steps live in :mod:`tests.bdd.steps.mcp_recommend_steps`. Scenarios drive
the production MCP adapter ``kairix.agents.mcp.server.tool_recommend`` with
deps + flag_reader injected — no @patch, no env vars.
"""

from pathlib import Path

import pytest
from pytest_bdd import scenarios

FEATURE = str(Path(__file__).parent / "features" / "mcp_recommend_capabilities.feature")

pytestmark = pytest.mark.bdd

scenarios(FEATURE)
