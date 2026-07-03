"""Step definitions for setup_wizard_remote_access.feature (#500).

F46-compliant composition: every scenario builds the real ASGI app
through ``kairix.agents.mcp.transport.build_mcp_app`` with the canonical
fakes from ``tests/fakes.py`` injected through the public seams
(``setup_service_factory`` / ``setup_secrets``), then drives it with
Starlette's TestClient. The "docker bridge" browser is
modelled by a non-loopback client address — the unit-level stand-in for the
bridge gateway IP a stock-Docker peer presents (the real bridge-source-IP
proof lives in the Linux-only fresh-install-smoke browser stage).

F1/F2-clean: no monkey-patching, no env-var manipulation; the operator
token is supplied via a populated ``FakeSecretsLoader``.
"""

from __future__ import annotations

from typing import Any

import pytest
from pytest_bdd import given, then, when

from kairix.agents.mcp.transport import build_mcp_app
from tests.fakes import (
    FakeMcpTransportServer,
    FakeSecretsLoader,
    FakeSetupService,
)

pytestmark = pytest.mark.bdd

_OPERATOR_TOKEN_IDENTITY = ("infra", "operator", None, "token")
# Fixture operator token, not a real credential.
_FAKE_TOKEN = "fake-operator-token"  # pragma: allowlist secret — fake fixture
_LOOPBACK = ("127.0.0.1", 9999)
_BRIDGE = ("172.18.0.1", 4242)


@pytest.fixture
def _remote_state() -> dict[str, Any]:
    """Per-scenario state: the composed app + the last response."""
    return {}


def _app() -> Any:
    return build_mcp_app(
        FakeMcpTransportServer(),
        setup_service_factory=lambda: FakeSetupService(),
        setup_secrets=FakeSecretsLoader(values={_OPERATOR_TOKEN_IDENTITY: _FAKE_TOKEN}),
    )


@given("the setup wizard is reachable only with an operator token")
def _wizard_token_guarded(_remote_state: dict[str, Any]) -> None:
    _remote_state["app"] = _app()


@when("a browser on the docker bridge opens the tokened wizard URL")
def _open_tokened_url(_remote_state: dict[str, Any]) -> None:
    from starlette.testclient import TestClient

    client = TestClient(_remote_state["app"], client=_BRIDGE)
    _remote_state["client"] = client
    _remote_state["response"] = client.get(
        f"/setup/?operator_token={_FAKE_TOKEN}",
        follow_redirects=False,
    )


@when("a browser on the docker bridge opens the wizard without the tokened URL")
def _open_without_token(_remote_state: dict[str, Any]) -> None:
    from starlette.testclient import TestClient

    client = TestClient(_remote_state["app"], client=_BRIDGE)
    _remote_state["response"] = client.get("/setup/provider")


@when("the operator opens the wizard from the host shell on loopback")
def _open_loopback(_remote_state: dict[str, Any]) -> None:
    from starlette.testclient import TestClient

    client = TestClient(_remote_state["app"], client=_LOOPBACK)
    _remote_state["response"] = client.get("/setup/provider")


@then("the wizard grants a session cookie and sends the browser to the start")
def _grant_redirect(_remote_state: dict[str, Any]) -> None:
    response = _remote_state["response"]
    assert response.status_code == 303
    assert response.headers["location"] == "/setup/"
    set_cookie = response.headers["set-cookie"]
    assert "kairix_operator_grant" in set_cookie
    # The raw operator token never rides the cookie (F15).
    assert _FAKE_TOKEN not in set_cookie


@when("the browser opens the provider step carrying that cookie")
def _open_provider_with_cookie(_remote_state: dict[str, Any]) -> None:
    client = _remote_state["client"]
    # The TestClient jar retained the Set-Cookie from the grant redirect.
    _remote_state["response"] = client.get("/setup/provider")


@then("the provider step is shown")
def _provider_shown(_remote_state: dict[str, Any]) -> None:
    response = _remote_state["response"]
    assert response.status_code == 200
    assert "Choose an AI provider" in response.text


@then("access is refused with guidance to open the tokened URL")
def _refused_with_guidance(_remote_state: dict[str, Any]) -> None:
    response = _remote_state["response"]
    assert response.status_code == 403
    assert "operator_token=" in response.text
