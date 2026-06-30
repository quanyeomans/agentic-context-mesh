"""pytest-bdd binder for mcp_expand.feature.

Steps live in :mod:`tests.bdd.steps.mcp_expand_steps`. Every scenario
composes through the public MCP tool handler
(``kairix.agents.mcp.server.tool_expand``) with deps injected through the
public seam — no direct pipeline construction, no monkeypatching (F1).
"""

from pathlib import Path

import pytest
from pytest_bdd import scenarios

FEATURE = str(Path(__file__).parent / "features" / "mcp_expand.feature")

pytestmark = pytest.mark.bdd

scenarios(FEATURE)
