"""Step definitions for setup_wizard_source_oauth.feature (#489).

F46-compliant composition: every scenario builds the real ASGI app
through ``kairix.agents.mcp.transport.build_mcp_app`` with the
canonical fakes from ``tests/fakes.py`` injected through the public
seams, then drives it with Starlette's TestClient from a loopback
client address — the laptop-first shape the wizard ships for.

F1/F2-clean: no monkey-patching, no env-var manipulation; the source
sign-in outcomes are scripted on ``FakeSetupService``.
"""

from __future__ import annotations

from typing import Any

import pytest
from pytest_bdd import given, then, when

from kairix.agents.mcp.transport import build_mcp_app
from kairix.platform.setup.service import SourceAuthStatus
from tests.fakes import (
    FakeMcpTransportServer,
    FakeSecretsLoader,
    FakeSetupService,
)

pytestmark = pytest.mark.bdd

_LOOPBACK = ("127.0.0.1", 9999)
# Fixture credentials for the fake service, not real values.
_WORKSPACE_FORM = {
    "provider": "slack",
    "workspace": "alpha",
    "client_id": "fake-id",
    "client_secret": "fake-secret-for-tests",  # pragma: allowlist secret
}
_CONSENT_URL = "https://provider.test/consent"


@pytest.fixture
def _source_state() -> dict[str, Any]:
    """Per-scenario state: the composed client + the last response."""
    return {}


def _compose_source_client(state: dict[str, Any], service: FakeSetupService) -> None:
    from starlette.testclient import TestClient

    app = build_mcp_app(
        FakeMcpTransportServer(),
        setup_service_factory=lambda: service,
        setup_secrets=FakeSecretsLoader(),
        setup_wizard_enabled=lambda: True,
    )
    state["service"] = service
    state["client"] = TestClient(app, client=_LOOPBACK)


@given("the setup wizard is enabled with a wizard backend ready to connect sources")
def _wizard_ready_for_sources(_source_state: dict[str, Any]) -> None:
    statuses = (
        SourceAuthStatus(provider="slack", phase="consent", authorize_url=_CONSENT_URL, error=None),
        SourceAuthStatus(provider="slack", phase="done", authorize_url=_CONSENT_URL, error=None),
    )
    _compose_source_client(_source_state, FakeSetupService(source_auth_statuses=statuses))


@given("the setup wizard is enabled with a wizard backend whose source sign-in was cancelled")
def _wizard_with_cancelled_signin(_source_state: dict[str, Any]) -> None:
    cancelled = SourceAuthStatus(
        provider="slack",
        phase="failed",
        authorize_url=None,
        error=(
            "The sign-in was cancelled on the provider's consent screen."
            " fix: approve the consent screen so kairix can read this source."
            " next: go back to the source step and start the connection again."
        ),
    )
    _compose_source_client(_source_state, FakeSetupService(source_auth_statuses=(cancelled,)))


@given("the setup wizard is enabled with a wizard backend with no sign-in in progress")
def _wizard_with_no_pending_signin(_source_state: dict[str, Any]) -> None:
    _compose_source_client(_source_state, FakeSetupService(callback_ok=False))


@when("the operator opens the source step")
def _open_source_step(_source_state: dict[str, Any]) -> None:
    _source_state["response"] = _source_state["client"].get("/setup/source")


@then("the source step offers a folder and the connectable sources")
def _source_step_offers(_source_state: dict[str, Any]) -> None:
    response = _source_state["response"]
    assert response.status_code == 200
    for label in ("Folder", "Slack", "GitHub", "Google Drive", "Gmail", "Google Calendar"):
        assert label in response.text


@when("the operator opens the connect form for the chat workspace")
def _open_connect_form(_source_state: dict[str, Any]) -> None:
    _source_state["response"] = _source_state["client"].get("/setup/source/connect", params={"provider": "slack"})


@then("the connect form shows the exact address to register with the provider")
def _connect_form_shows_redirect(_source_state: dict[str, Any]) -> None:
    response = _source_state["response"]
    assert response.status_code == 200
    assert "http://testserver/setup/oauth/callback" in response.text
    assert "Redirect URL to register" in response.text


@when("the operator submits the workspace connection details")
def _submit_connection_details(_source_state: dict[str, Any]) -> None:
    _source_state["response"] = _source_state["client"].post(
        "/setup/source/connect",
        data=_WORKSPACE_FORM,
        follow_redirects=True,
    )


@then("the wizard waits for the provider sign-in to finish")
def _wizard_waits(_source_state: dict[str, Any]) -> None:
    response = _source_state["response"]
    assert response.status_code == 200
    assert "Finishing the sign-in" in response.text
    # The connection details reached the backend with the live origin.
    provider, fields, origin = _source_state["service"].source_auth_starts[0]
    assert provider == "slack"
    assert fields["workspace"] == "alpha"
    assert origin == "http://testserver"


@when("the provider sends the browser back with an approval")
def _provider_redirects_back(_source_state: dict[str, Any]) -> None:
    _source_state["response"] = _source_state["client"].get(
        "/setup/oauth/callback",
        params={"code": "fake-code", "state": "fake-nonce"},
        follow_redirects=True,
    )
    assert _source_state["service"].callback_deliveries


@when("the sign-in finishes")
def _signin_finishes(_source_state: dict[str, Any]) -> None:
    client = _source_state["client"]
    # Poll like the browser does; the scripted statuses end at done,
    # whose HX-Redirect points at the picker.
    for _ in range(5):
        response = client.get("/setup/source/auth-status")
        target = response.headers.get("HX-Redirect", "")
        if target.startswith("/setup/source/picker"):
            _source_state["response"] = client.get(target)
            return
    raise AssertionError("sign-in never finished after 5 status polls")


@then("the picker lists the channels the workspace offers")
def _picker_lists_channels(_source_state: dict[str, Any]) -> None:
    response = _source_state["response"]
    assert response.status_code == 200
    assert "#general" in response.text
    assert "#engineering" in response.text


@when("the operator picks two channels and saves")
def _pick_and_save(_source_state: dict[str, Any]) -> None:
    _source_state["response"] = _source_state["client"].post(
        "/setup/source/save",
        data={"provider": "slack", "unit": ["C001", "C002"]},
    )


@then("the wizard states what will be fetched before anything is downloaded")
def _states_what_will_be_fetched(_source_state: dict[str, Any]) -> None:
    response = _source_state["response"]
    assert response.status_code == 200
    assert "2 channels selected" in response.text
    assert "nothing is downloaded until indexing runs" in response.text.lower()
    assert _source_state["service"].saved_oauth_sources == [("slack", "", ("C001", "C002"))]


@when("the operator opens the wait screen for the chat workspace")
def _open_wait_screen(_source_state: dict[str, Any]) -> None:
    _source_state["response"] = _source_state["client"].get("/setup/source/wait", params={"provider": "slack"})
    assert _source_state["response"].status_code == 200


@when("the wizard checks the sign-in progress")
def _check_signin_progress(_source_state: dict[str, Any]) -> None:
    _source_state["response"] = _source_state["client"].get("/setup/source/auth-status")


@then("the wizard explains the sign-in was cancelled and how to retry")
def _explains_cancellation(_source_state: dict[str, Any]) -> None:
    response = _source_state["response"]
    assert response.status_code == 200
    assert "cancelled" in response.text
    assert "Fix" in response.text
    assert "Next" in response.text


@when("a sign-in response arrives without a connection waiting")
def _stray_callback(_source_state: dict[str, Any]) -> None:
    _source_state["response"] = _source_state["client"].get(
        "/setup/oauth/callback",
        params={"code": "stray-code"},
        follow_redirects=False,
    )


@then("the wizard turns it away and explains how to start a connection")
def _stray_callback_rejected(_source_state: dict[str, Any]) -> None:
    response = _source_state["response"]
    assert response.status_code == 409
    assert "No source connection is waiting" in response.text
    assert "fix:" in response.text
