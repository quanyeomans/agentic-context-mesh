"""Step definitions for setup_wizard.feature.

F46-compliant composition: every scenario builds the real ASGI app
through ``kairix.agents.mcp.transport.build_mcp_app`` with the
canonical fakes from ``tests/fakes.py`` injected through the public
seams (``setup_service_factory`` / ``setup_secrets``), then drives it
with Starlette's TestClient from a loopback client address — exactly
the laptop-first shape the wizard ships for. The wizard is always
mounted (the ``setup_wizard_web`` cutover flag retired, PLA-287).

F1/F2-clean: no monkey-patching, no env-var manipulation.
"""

from __future__ import annotations

from typing import Any

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.agents.mcp.transport import build_mcp_app
from tests.fakes import (
    FakeMcpTransportServer,
    FakeSecretsLoader,
    FakeSetupService,
)

pytestmark = pytest.mark.bdd

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


def _compose_client(state: dict[str, Any], *, service: FakeSetupService) -> None:
    from starlette.testclient import TestClient

    app = build_mcp_app(
        FakeMcpTransportServer(),
        setup_service_factory=lambda: service,
        setup_secrets=FakeSecretsLoader(),
    )
    state["service"] = service
    state["client"] = TestClient(app, client=_LOOPBACK)


@given("the setup wizard is enabled with a ready wizard backend")
def _wizard_enabled_ready(_wizard_state: dict[str, Any]) -> None:
    _compose_client(_wizard_state, service=FakeSetupService())


@given("the setup wizard is enabled with a wizard backend that rejects provider keys")
def _wizard_enabled_rejecting(_wizard_state: dict[str, Any]) -> None:
    _compose_client(_wizard_state, service=FakeSetupService(validate_ok=False))


@given("the setup wizard is enabled with a wizard backend whose config file cannot be written")
def _wizard_enabled_read_only_config(_wizard_state: dict[str, Any]) -> None:
    read_only = OSError(30, "Read-only file system", "/etc/kairix/kairix.config.yaml")
    _compose_client(
        _wizard_state,
        service=FakeSetupService(save_provider_raises=read_only, save_source_raises=read_only),
    )


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


@when("the operator opens the key step for an Azure provider")
def _open_azure_key_step(_wizard_state: dict[str, Any]) -> None:
    _wizard_state["response"] = _wizard_state["client"].get("/setup/key", params={"provider": "azure_foundry"})


@then("the key step offers a deployment name field")
def _key_step_offers_deployment_field(_wizard_state: dict[str, Any]) -> None:
    response = _wizard_state["response"]
    assert response.status_code == 200
    assert "Deployment name" in response.text
    assert "Azure gives each model you deploy its own name" in response.text


@when(parsers.parse('the operator validates their provider key with the deployment name "{name}"'))
def _validate_key_with_deployment(_wizard_state: dict[str, Any], name: str) -> None:
    _wizard_state["response"] = _wizard_state["client"].post(
        "/setup/key/validate",
        data={
            "provider": "azure_foundry",
            "api_key": "fake-key-for-tests",  # pragma: allowlist secret
            "endpoint": "https://res.services.ai.azure.com",
            "deployment": name,
        },
    )
    # The deployment name reached the service alongside the credential.
    assert _wizard_state["service"].validate_calls[-1][3] == name


@then("the wizard explains the config file is read-only and how to make saves stick")
def _read_only_config_rescue(_wizard_state: dict[str, Any]) -> None:
    response = _wizard_state["response"]
    assert response.status_code == 200
    assert "read-only" in response.text
    assert "KAIRIX_CONFIG_OVERLAY_PATH" in response.text
    assert "fix:" in response.text
    assert "next:" in response.text


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


@then("the wizard advances to the capability tour")
def _on_tour(_wizard_state: dict[str, Any]) -> None:
    assert _wizard_state["redirect_target"] == "/setup/tour"
    response = _wizard_state["response"]
    assert response.status_code == 200
    assert "See what your agents can do" in response.text


@when(parsers.parse('the operator searches for "{query}"'))
def _search_for(_wizard_state: dict[str, Any], query: str) -> None:
    _wizard_state["response"] = _wizard_state["client"].post("/setup/search", data={"query": query})


@then("the search shows results from their documents")
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
    # The displayed address re-anchors on the origin the operator's
    # browser reached (review L1) — the test client's origin here.
    assert "http://testserver/mcp" in response.text
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


# ---------------------------------------------------------------------------
# Capability tour (#490)
# ---------------------------------------------------------------------------


@given("the setup wizard is enabled with a wizard backend over a freshly indexed knowledge store")
def _wizard_enabled_fresh_store(_wizard_state: dict[str, Any]) -> None:
    # A fresh knowledge store has nothing to brief on yet — the briefing
    # sample must explain that honestly rather than fabricate content.
    _compose_client(_wizard_state, service=FakeSetupService(tour_brief_preview=""))


@when("the operator opens the capability tour")
def _open_tour(_wizard_state: dict[str, Any]) -> None:
    _wizard_state["response"] = _wizard_state["client"].get("/setup/tour")


@then("the tour offers five sample runs, each naming the tool agents use for it")
def _tour_offers_five(_wizard_state: dict[str, Any]) -> None:
    response = _wizard_state["response"]
    assert response.status_code == 200
    assert response.text.count("Run it") == 5
    for tool in ("search", "prep", "memory_write", "brief", "timeline"):
        assert f'<code class="kx-tool-tag">{tool}</code>' in response.text


@when("the operator runs the context pack sample")
def _run_context_pack(_wizard_state: dict[str, Any]) -> None:
    _wizard_state["response"] = _wizard_state["client"].post(
        "/setup/tour/prep",
        data={"query": "What do my documents say about current projects?"},
    )


@then("the context pack sample shows a summary built from their documents")
def _context_pack_shows_summary(_wizard_state: dict[str, Any]) -> None:
    response = _wizard_state["response"]
    assert response.status_code == 200
    assert "rollout plan is the main thread" in response.text
    assert "notes/kickoff.md" in response.text


@when("the operator runs the remember sample")
def _run_remember(_wizard_state: dict[str, Any]) -> None:
    _wizard_state["response"] = _wizard_state["client"].post(
        "/setup/tour/remember",
        data={"content": "Setup finished today — this knowledge store is live."},
    )


@then("the remember sample shows the memory was saved and found again by search")
def _remember_shows_roundtrip(_wizard_state: dict[str, Any]) -> None:
    response = _wizard_state["response"]
    assert response.status_code == 200
    assert "Saved, then found by search" in response.text
    assert "ms" in response.text


@when("the operator runs the briefing sample")
def _run_briefing(_wizard_state: dict[str, Any]) -> None:
    _wizard_state["response"] = _wizard_state["client"].post("/setup/tour/brief")


@then("the briefing sample shows a briefing built from recent activity")
def _briefing_shows_content(_wizard_state: dict[str, Any]) -> None:
    response = _wizard_state["response"]
    assert response.status_code == 200
    assert "the rollout kicked off and two decisions landed" in response.text


@then("the briefing sample explains the brief fills in as the team works")
def _briefing_explains_empty(_wizard_state: dict[str, Any]) -> None:
    response = _wizard_state["response"]
    assert response.status_code == 200
    assert "your brief gets richer as your team works" in response.text


@when("the operator runs the timeline sample")
def _run_timeline_sample(_wizard_state: dict[str, Any]) -> None:
    _wizard_state["response"] = _wizard_state["client"].post(
        "/setup/tour/timeline",
        data={"query": "What changed in the last week?"},
    )


@then("the timeline sample shows recent activity with dates")
def _timeline_shows_dates(_wizard_state: dict[str, Any]) -> None:
    response = _wizard_state["response"]
    assert response.status_code == 200
    assert "2026-06-08" in response.text
    assert "Sprint planning" in response.text


@then("the finish screen names the tool agents use for each capability")
def _finish_names_tools(_wizard_state: dict[str, Any]) -> None:
    response = _wizard_state["response"]
    assert response.status_code == 200
    for tool in ("search", "prep", "memory_write", "brief", "timeline"):
        assert f"<code>{tool}</code>" in response.text
