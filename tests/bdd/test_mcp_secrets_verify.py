"""Pytest entry point for mcp_secrets_verify.feature."""

from __future__ import annotations

import pytest
from pytest_bdd import scenarios

pytestmark = pytest.mark.bdd

scenarios("features/mcp_secrets_verify.feature")
