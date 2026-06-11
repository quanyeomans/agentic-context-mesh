"""Step definitions for setup_wizard.feature + feature_flag_setup_wizard_web.feature.

F46-compliant composition: every scenario builds the real ASGI app
through ``kairix.agents.mcp.transport.build_mcp_app`` with the
canonical fakes from ``tests/fakes.py`` injected through the public
seams (``setup_service_factory`` / ``setup_secrets`` /
``setup_wizard_enabled``), then drives it with Starlette's TestClient
from a loopback client address — exactly the laptop-first shape the
wizard ships for.

F1/F2-clean: no monkey-patching, no env-var manipulation; the flag is
pinned per scenario via ``FakeFeatureFlagResolver``.
"""

from __future__ import annotations

from typing import Any

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.agents.mcp.transport import build_mcp_app
from tests.fakes import (
    FakeFeatureFlagResolver,
    FakeMcpTransportServer,
    FakeSecretsLoader,
    FakeSetupService,
)

pytestmark = pytest.mark.bdd

_FLAG_NAME = "setup_wizard_web"
_LOOPBACK = ("127.0.0.1", 9999)
# Fixture credential for the fake service, not a real key.
_SAVE_KEY_PAYLOAD = {
    "provider": "anthropic",
    "api_key": "fake-key-for-tests",  # pragma: allowlist secret
    "endpoint": "",
    "model": "model-alpha",
}


@pytest.fixture
def _wizard_state() -> dict[str, Any]:
    """Per-scenario state: the composed client + the last response."""
    return {}


def _compose_client(state: dict[str, Any], *, flag_on: bool, service: FakeSetupService) -> None:
    from starlette.testclient import TestClient

    resolver = FakeFeatureFlagResolver().with_flag(_FLAG_NAME, flag_on)
    app = build_mcp_app(
        FakeMcpTransportServer(),
        setup_service_factory=lambda: service,
        setup_secrets=FakeSecretsLoader(),
        setup_wizard_enabled=lambda: resolver.get(_FLAG_NAME),
    )
    state["service"] = service
    state["client"] = TestClient(app, client=_LOOPBACK)


@given("the setup wizard is enabled with a ready wizard backend")
def _wizard_enabled_ready(_wizard_state: dict[str, Any]) -> None:
    _compose_client(_wizard_state, flag_on=True, service=FakeSetupService())


@given("the setup wizard is enabled with a wizard backend that rejects provider keys")
def _wizard_enabled_rejecting(_wizard_state: dict[str, Any]) -> None:
    _compose_client(_wizard_state, flag_on=True, service=FakeSetupService(validate_ok=False))


@given("the setup wizard flag is ON")
def _wizard_flag_on(_wizard_state: dict[str, Any]) -> None:
    _compose_client(_wizard_state, flag_on=True, service=FakeSetupService())


@given("the setup wizard flag is OFF")
def _wizard_flag_off(_wizard_state: dict[str, Any]) -> None:
    _compose_client(_wizard_state, flag_on=False, service=FakeSetupService())


@when("the operator opens the setup wizard")
@when("the operator requests the setup wizard")
def _open_wizard(_wizard_state: dict[str, Any]) -> None:
    _wizard_state["response"] = _wizard_state["client"].get("/setup", follow_redirects=True)


@then("the welcome screen invites them to get started")
def _welcome_invites(_wizard_state: dict[str, Any]) -> None:
    response = _wizard_state["response"]
    assert response.status_code == 200, f"expected 200, got {response.status_code}"
    assert "Welcome to kairix" in response.text
    assert "Get started" in response.text


@then("the server reports there is no such page")
def _no_such_page(_wizard_state: dict[str, Any]) -> None:
    assert _wizard_state["response"].status_code == 404


@when("the operator continues to the provider step")
def _continue_to_provider(_wizard_state: dict[str, Any]) -> None:
    _wizard_state["response"] = _wizard_state["client"].get("/setup/provider")


@then("the provider step lists the available AI providers")
def _provider_step_lists(_wizard_state: dict[str, Any]) -> None:
    response = _wizard_state["response"]
    assert response.status_code == 200
    assert "Choose an AI provider" in response.text
    assert "anthropic" in response.text


@when("the operator validates their provider key")
def _validate_key(_wizard_state: dict[str, Any]) -> None:
    _wizard_state["response"] = _wizard_state["client"].post(
        "/setup/key/validate",
        data={"provider": "anthropic", "api_key": "fake-key-for-tests", "endpoint": ""},  # pragma: allowlist secret
    )


@then("the key is accepted and the available models are shown")
def _key_accepted(_wizard_state: dict[str, Any]) -> None:
    response = _wizard_state["response"]
    assert response.status_code == 200
    assert "Key validated successfully" in response.text
    assert "model-alpha" in response.text


@then("the key is rejected with guidance to fix and retry")
def _key_rejected_with_guidance(_wizard_state: dict[str, Any]) -> None:
    response = _wizard_state["response"]
    assert response.status_code == 200
    assert "rejected by the provider" in response.text
    assert "Fix" in response.text
    assert "Next" in response.text


@when("the operator saves the provider key")
def _save_key(_wizard_state: dict[str, Any]) -> None:
    _wizard_state["response"] = _wizard_state["client"].post(
        "/setup/key",
        data=_SAVE_KEY_PAYLOAD,
        follow_redirects=True,
    )


@then("the wizard advances to the folder step")
def _on_folder_step(_wizard_state: dict[str, Any]) -> None:
    response = _wizard_state["response"]
    assert response.status_code == 200
    assert "Choose your first source" in response.text


@when(parsers.parse('the operator scans the folder "{folder}"'))
def _scan_folder(_wizard_state: dict[str, Any], folder: str) -> None:
    _wizard_state["folder"] = folder
    _wizard_state["response"] = _wizard_state["client"].post("/setup/folder/scan", data={"folder_path": folder})


@then("the scan reports the files found and the estimated cost")
def _scan_reports(_wizard_state: dict[str, Any]) -> None:
    response = _wizard_state["response"]
    assert response.status_code == 200
    assert "Scan complete" in response.text
    assert "Files found" in response.text
    assert "Estimated cost" in response.text


@when("the operator starts indexing")
def _start_indexing(_wizard_state: dict[str, Any]) -> None:
    _wizard_state["response"] = _wizard_state["client"].post(
        "/setup/folder",
        data={"folder_path": _wizard_state["folder"]},
        follow_redirects=True,
    )
    assert "Indexing your documents" in _wizard_state["response"].text


@when("the indexing run completes")
def _indexing_completes(_wizard_state: dict[str, Any]) -> None:
    client = _wizard_state["client"]
    # Poll like the browser does (the screen refreshes every second);
    # the fake advances one tick per poll so this terminates quickly.
    for _ in range(10):
        response = client.get("/setup/indexing/progress")
        if "HX-Redirect" in response.headers:
            _wizard_state["redirect_target"] = response.headers["HX-Redirect"]
            _wizard_state["response"] = client.get(response.headers["HX-Redirect"])
            return
    raise AssertionError("indexing never completed after 10 progress polls")


@then("the wizard advances to the first search")
def _on_first_search(_wizard_state: dict[str, Any]) -> None:
    assert _wizard_state["redirect_target"] == "/setup/first-search"
    response = _wizard_state["response"]
    assert response.status_code == 200
    assert "Try your first search" in response.text


@when(parsers.parse('the operator searches for "{query}"'))
def _search_for(_wizard_state: dict[str, Any], query: str) -> None:
    _wizard_state["response"] = _wizard_state["client"].post("/setup/search", data={"query": query})


@then("the first search shows results from their documents")
def _search_shows_results(_wizard_state: dict[str, Any]) -> None:
    response = _wizard_state["response"]
    assert response.status_code == 200
    assert "Project kickoff notes" in response.text
    assert "% match" in response.text


@when("the operator opens the connect-agent step")
def _open_connect_agent(_wizard_state: dict[str, Any]) -> None:
    _wizard_state["response"] = _wizard_state["client"].get("/setup/connect-agent")


@then("the connect-agent step shows the address agents use to connect")
def _connect_agent_shows_url(_wizard_state: dict[str, Any]) -> None:
    response = _wizard_state["response"]
    assert response.status_code == 200
    assert "http://127.0.0.1:8765/mcp" in response.text
    assert "Claude Code" in response.text


@when("the operator verifies the agent connection")
def _verify_connection(_wizard_state: dict[str, Any]) -> None:
    _wizard_state["response"] = _wizard_state["client"].post("/setup/connect-agent/verify")


@then("the wizard confirms the connection with the available tool count")
def _connection_confirmed(_wizard_state: dict[str, Any]) -> None:
    response = _wizard_state["response"]
    assert response.status_code == 200
    assert "Agent connected" in response.text
    assert "tools available" in response.text


@when("the operator opens the finish screen")
def _open_finish(_wizard_state: dict[str, Any]) -> None:
    _wizard_state["response"] = _wizard_state["client"].get("/setup/done")


@then("the finish screen celebrates the indexed knowledge")
def _finish_celebrates(_wizard_state: dict[str, Any]) -> None:
    response = _wizard_state["response"]
    assert response.status_code == 200
    assert "Your knowledge is ready" in response.text
    assert "chunks indexed" in response.text
