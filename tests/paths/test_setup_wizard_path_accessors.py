"""Unit tests for the setup-wizard paths accessors (#485 / #486).

``config_overlay_path_override`` surfaces ``KAIRIX_CONFIG_OVERLAY_PATH``
to the wizard's config WRITER (the layered loader already reads it);
``container_source_prefill`` resolves the folder-step pre-fill inside a
container. Per F4 the env reads live in ``kairix/paths.py``; tests
drive the parsing through the ``environ=`` injection seam to stay
F2-clean (no ``monkeypatch.setenv("KAIRIX_*")``).
"""

from __future__ import annotations

import pytest

from kairix.paths import (
    config_overlay_path_override,
    container_source_prefill,
    mcp_bind_host,
    wizard_tokened_url,
)

pytestmark = pytest.mark.unit


def test_overlay_override_returns_the_configured_path() -> None:
    value = config_overlay_path_override(
        environ={"KAIRIX_CONFIG_OVERLAY_PATH": "/var/lib/kairix/kairix.config.local.yaml"},
    )
    assert value == "/var/lib/kairix/kairix.config.local.yaml"


def test_overlay_override_is_none_when_unset_or_blank() -> None:
    assert config_overlay_path_override(environ={}) is None
    assert config_overlay_path_override(environ={"KAIRIX_CONFIG_OVERLAY_PATH": ""}) is None


def test_container_prefill_defaults_to_the_stock_compose_mount() -> None:
    assert container_source_prefill({"KAIRIX_CONTAINER": "1"}) == "/data/documents"


def test_container_prefill_honours_the_configured_document_root() -> None:
    env = {"KAIRIX_CONTAINER": "1", "KAIRIX_DOCUMENT_ROOT": "/srv/knowledge"}
    assert container_source_prefill(env) == "/srv/knowledge"


def test_container_prefill_is_none_outside_a_container() -> None:
    assert container_source_prefill({}) is None
    assert container_source_prefill({"KAIRIX_DOCUMENT_ROOT": "/srv/knowledge"}) is None


# ---------------------------------------------------------------------------
# Tokened-URL bind-host accessor (#500)
# ---------------------------------------------------------------------------


def test_bind_host_defaults_to_localhost_when_unset() -> None:
    assert mcp_bind_host(environ={}) == "localhost"
    assert mcp_bind_host(environ={"KAIRIX_MCP_BIND_HOST": ""}) == "localhost"


def test_bind_host_returns_the_configured_host() -> None:
    assert mcp_bind_host(environ={"KAIRIX_MCP_BIND_HOST": "kairix.example.internal"}) == "kairix.example.internal"


def test_bind_host_normalises_bind_any_to_localhost() -> None:
    """``0.0.0.0`` / ``::`` are bind directives, not reachable hosts — the
    printed URL must use a host an operator can actually open."""
    assert mcp_bind_host(environ={"KAIRIX_MCP_BIND_HOST": "0.0.0.0"}) == "localhost"
    assert mcp_bind_host(environ={"KAIRIX_MCP_BIND_HOST": "::"}) == "localhost"


def test_tokened_url_interpolates_host_port_and_token() -> None:
    url = wizard_tokened_url(token="grant-abc", host="example.internal", port=8443)
    assert url == "http://example.internal:8443/setup/?operator_token=grant-abc"


def test_tokened_url_resolves_host_from_the_environ_seam() -> None:
    url = wizard_tokened_url(
        token="grant-xyz",
        port=8080,
        environ={"KAIRIX_MCP_BIND_HOST": "kairix.example.internal"},
    )
    assert url == "http://kairix.example.internal:8080/setup/?operator_token=grant-xyz"
