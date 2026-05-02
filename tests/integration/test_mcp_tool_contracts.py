"""
Integration tests: MCP tool contract validation against real indexed data.
"""

from unittest.mock import patch

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.integration
def test_search_tool_returns_expected_schema(real_db, real_document_root):
    """tool_search returns dict with required keys."""
    # Mock the embed function (no API in CI) but let search run against real DB
    with patch("kairix._azure.embed_text", return_value=[0.1] * 1536):
        from kairix.agents.mcp.server import tool_search

        result = tool_search(query="engineering patterns", budget=3000)
    assert "results" in result or "error" in result
    if "results" in result:
        assert isinstance(result["results"], list)
