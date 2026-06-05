"""Unit tests for :func:`kairix.paths.mcp_endpoint` + ``mcp_routing_enabled``.

The functions read the operator-configured MCP endpoint and the
routing-enabled flag for the CLI-via-MCP shortcut (#411). Per F4 the
env-var read lives in ``kairix/paths.py``; tests drive the parsing
through the ``environ=`` injection seam to stay F2-clean (no
``monkeypatch.setenv("KAIRIX_*")``).

Sabotage proofs:

* ``test_default_endpoint_when_env_unset``: inline ``""`` in place of
  ``_DEFAULT_MCP_ENDPOINT`` → the default assertion fails.
* ``test_env_override_wins``: have ``mcp_endpoint`` ignore the
  ``environ`` kwarg and always read ``os.environ`` → the empty-environ
  override assertion fails because process env may still have the var
  set.
* ``test_routing_disabled_via_env``: invert the truthy check in
  ``mcp_routing_enabled`` → at least one of the four falsy variants
  asserts the wrong way.
"""

from __future__ import annotations

import pytest

from kairix.paths import mcp_endpoint, mcp_routing_enabled

pytestmark = pytest.mark.unit


def test_default_endpoint_when_env_unset() -> None:
    """No env var → default ``http://localhost:8080/mcp``.

    Pass an empty environ explicitly so the test never depends on the
    process env (which may have ``KAIRIX_MCP_ENDPOINT`` set by the
    operator running the suite).
    """
    assert mcp_endpoint(environ={}) == "http://localhost:8080/mcp"


def test_default_endpoint_can_be_overridden_by_caller() -> None:
    """The ``default=`` kwarg lets callers thread their own default."""
    assert mcp_endpoint(default="http://other:9000/mcp", environ={}) == "http://other:9000/mcp"


def test_env_override_wins() -> None:
    """``KAIRIX_MCP_ENDPOINT`` overrides the default.

    F2-clean: drives the env-read parsing through the ``environ=``
    injection seam — no ``monkeypatch.setenv("KAIRIX_MCP_ENDPOINT", ...)``.
    """
    env = {"KAIRIX_MCP_ENDPOINT": "http://operator-set:7443/mcp"}
    assert mcp_endpoint(environ=env) == "http://operator-set:7443/mcp"


def test_empty_string_env_falls_back_to_default() -> None:
    """An empty string in the env var falls back to the default."""
    env = {"KAIRIX_MCP_ENDPOINT": ""}
    assert mcp_endpoint(environ=env) == "http://localhost:8080/mcp"


def test_routing_enabled_by_default() -> None:
    """No env var → routing enabled."""
    assert mcp_routing_enabled(environ={}) is True


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "off", "no", "OFF", "False"])
def test_routing_disabled_via_falsy_env(value: str) -> None:
    """Operator can disable routing with any falsy variant."""
    assert mcp_routing_enabled(environ={"KAIRIX_MCP_ROUTING": value}) is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "on", "yes", "anything-else"])
def test_routing_enabled_via_truthy_env(value: str) -> None:
    """Any non-falsy value keeps routing on (truthy variant + anything-else)."""
    assert mcp_routing_enabled(environ={"KAIRIX_MCP_ROUTING": value}) is True


def test_routing_disabled_handles_whitespace() -> None:
    """Falsy values with surrounding whitespace still disable routing."""
    assert mcp_routing_enabled(environ={"KAIRIX_MCP_ROUTING": "  0  "}) is False
