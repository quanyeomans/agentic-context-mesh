"""Outcome tests for the flag-gated web setup wizard (#474).

Every test composes the real Starlette app through
``kairix.agents.mcp.transport.build_mcp_app`` with the canonical fakes
(``FakeSetupService`` / ``FakeSecretsLoader`` / ``FakeMcpTransportServer``
from ``tests/fakes.py``) injected through the public seams — no
monkey-patching (F1/F2-clean by construction). Assertions are on the
rendered HTML / headers (F30 spirit), not on status codes alone.
"""

from __future__ import annotations

import logging

import pytest

# starlette ships via the optional [agents] extra (transitive dep of mcp);
# CI's base-deps stages must skip rather than fail on the missing import.
starlette = pytest.importorskip("starlette")
from starlette.testclient import TestClient  # noqa: E402

from kairix.agents.mcp.transport import build_mcp_app  # noqa: E402
from kairix.platform.setup.service import IndexStatus  # noqa: E402
from tests.fakes import (  # noqa: E402
    FakeMcpTransportServer,
    FakeSecretsLoader,
    FakeSetupService,
)

pytestmark = pytest.mark.unit

_LOOPBACK = ("127.0.0.1", 9999)
# Fixture credential for the fake service, not a real key.
_SAVE_KEY_PAYLOAD = {
    "provider": "anthropic",
    "api_key": "fake-key-for-tests",  # pragma: allowlist secret
    "endpoint": "",
    "model": "model-alpha",
}
_OPERATOR_TOKEN_IDENTITY = ("infra", "operator", None, "token")
_TOKEN_HEADER = "X-Kairix-Operator-Token"


def _build_client(
    *,
    service: FakeSetupService | None = None,
    secrets: FakeSecretsLoader | None = None,
    flag_on: bool = True,
    client_addr: tuple[str, int] = _LOOPBACK,
    use_default_factory: bool = False,
    readiness_check: object = None,
) -> TestClient:
    """Compose the app through the production composer with fakes."""
    resolved_service = service if service is not None else FakeSetupService()
    kwargs: dict[str, object] = {}
    if not use_default_factory:
        # Omitting the kwarg entirely exercises the production default
        # (the lazy build_setup_service stub).
        kwargs["setup_service_factory"] = lambda: resolved_service
    app = build_mcp_app(
        FakeMcpTransportServer(),
        setup_secrets=secrets if secrets is not None else FakeSecretsLoader(),
        setup_wizard_enabled=lambda: flag_on,
        readiness_check=readiness_check,  # type: ignore[arg-type]  # F3 rationale: None or callable; build_mcp_app accepts both.
        **kwargs,  # type: ignore[arg-type]  # F3 rationale: heterogeneous kwargs dict for an optional seam; build_mcp_app validates the shape.
    )
    return TestClient(app, client=client_addr)


# ---------------------------------------------------------------------------
# Flag gating
# ---------------------------------------------------------------------------


def test_flag_off_means_no_setup_routes() -> None:
    client = _build_client(flag_on=False)
    assert client.get("/setup", follow_redirects=True).status_code == 404
    assert client.get("/setup/provider").status_code == 404
    # The MCP transport surface is unchanged.
    assert client.get("/mcp").status_code == 200


def test_flag_default_reader_keeps_wizard_off() -> None:
    """No ``setup_wizard_enabled`` seam → the registry default (OFF) wins."""
    app = build_mcp_app(FakeMcpTransportServer())
    client = TestClient(app, client=_LOOPBACK)
    assert client.get("/setup", follow_redirects=True).status_code == 404


# ---------------------------------------------------------------------------
# Screens
# ---------------------------------------------------------------------------


def test_welcome_screen_invites_the_operator() -> None:
    client = _build_client()
    response = client.get("/setup", follow_redirects=True)
    assert response.status_code == 200
    assert "Welcome to kairix" in response.text
    assert "Get started" in response.text
    assert "/setup/provider" in response.text


def test_welcome_screen_references_only_local_assets() -> None:
    """No CDN URLs — the container may be offline."""
    client = _build_client()
    response = client.get("/setup", follow_redirects=True)
    assert "/setup/static/pico.classless.min.css" in response.text
    assert "/setup/static/htmx.min.js" in response.text
    assert "unpkg.com" not in response.text


def test_provider_screen_lists_installed_provider_plugins() -> None:
    client = _build_client()
    response = client.get("/setup/provider")
    assert response.status_code == 200
    # Names come from the real plugin registry (entry points), not canned data.
    assert "anthropic" in response.text
    assert "openai" in response.text
    assert "Bring your own key" in response.text


def test_key_screen_requires_provider_selection() -> None:
    client = _build_client()
    response = client.get("/setup/key", follow_redirects=True)
    assert "Choose an AI provider" in response.text


def test_key_screen_names_the_chosen_provider() -> None:
    client = _build_client()
    response = client.get("/setup/key", params={"provider": "anthropic"})
    assert response.status_code == 200
    assert "anthropic" in response.text
    assert "Validate key" in response.text


def test_static_assets_are_served_from_the_package() -> None:
    client = _build_client()
    css = client.get("/setup/static/kairix.css")
    assert css.status_code == 200
    assert "kx-setup-card" in css.text
    assert client.get("/setup/static/htmx.min.js").status_code == 200
    assert client.get("/setup/static/pico.classless.min.css").status_code == 200


# ---------------------------------------------------------------------------
# Key validation + save
# ---------------------------------------------------------------------------


def test_key_validation_success_lists_models() -> None:
    service = FakeSetupService(models=("model-alpha", "model-beta"))
    client = _build_client(service=service)
    response = client.post(
        "/setup/key/validate",
        data={"provider": "anthropic", "api_key": "fake-key-for-tests", "endpoint": ""},  # pragma: allowlist secret
    )
    assert response.status_code == 200
    assert "Key validated successfully" in response.text
    assert "model-alpha" in response.text
    assert "model-beta" in response.text
    # The wizard passed the form through to the service unchanged
    # (empty endpoint normalised to None).
    assert service.validate_calls == [("anthropic", "fake-key-for-tests", None)]


def test_key_validation_failure_renders_guided_error() -> None:
    service = FakeSetupService(validate_ok=False)
    client = _build_client(service=service)
    response = client.post(
        "/setup/key/validate",
        data={"provider": "anthropic", "api_key": "fake-bad-key", "endpoint": ""},  # pragma: allowlist secret
    )
    assert response.status_code == 200
    assert "rejected by the provider" in response.text
    assert "Fix" in response.text
    assert "Next" in response.text
    # Internal rule IDs never appear in user copy.
    assert "F21" not in response.text


def test_api_key_never_echoed_or_logged(caplog: pytest.LogCaptureFixture) -> None:
    """F15 — the pasted key must not appear in any response body or log."""
    secret_key = "fake-key-do-not-echo-1234567890"  # pragma: allowlist secret
    service = FakeSetupService(validate_ok=False)
    client = _build_client(service=service)
    with caplog.at_level(logging.DEBUG):
        failure = client.post(
            "/setup/key/validate",
            data={"provider": "anthropic", "api_key": secret_key, "endpoint": ""},
        )
        success = client.post(
            "/setup/key/validate",
            data={"provider": "anthropic", "api_key": secret_key, "endpoint": ""},
        )
    assert secret_key not in failure.text
    assert secret_key not in success.text
    assert secret_key not in caplog.text


def test_key_save_persists_provider_and_advances_to_folder() -> None:
    service = FakeSetupService()
    client = _build_client(service=service)
    response = client.post(
        "/setup/key",
        data=_SAVE_KEY_PAYLOAD,
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/setup/folder"
    assert service.saved_providers == [("anthropic", "fake-key-for-tests", None, "model-alpha")]


# ---------------------------------------------------------------------------
# Folder scan + save
# ---------------------------------------------------------------------------


def test_folder_scan_renders_cost_estimate_card() -> None:
    service = FakeSetupService(scan_files=533, scan_words=3_200_000, scan_cost_usd=0.04)
    client = _build_client(service=service)
    response = client.post("/setup/folder/scan", data={"folder_path": "~/Documents"})
    assert response.status_code == 200
    assert "Scan complete" in response.text
    assert "533" in response.text
    assert "3,200,000" in response.text
    assert "$0.04" in response.text
    assert "~/Documents" in response.text
    # The prototype's bogus cache-hit promise was deliberately dropped.
    assert "cache hit" not in response.text


def test_folder_scan_failure_renders_guided_error() -> None:
    service = FakeSetupService(scan_ok=False)
    client = _build_client(service=service)
    response = client.post("/setup/folder/scan", data={"folder_path": "/nonexistent"})
    assert response.status_code == 200
    assert "not found or not readable" in response.text
    assert "Fix" in response.text
    # The success-only reveal hook must be absent so "Start indexing"
    # stays hidden after a failed scan.
    assert "kx-scan-result" not in response.text


def test_folder_save_records_source_and_starts_indexing() -> None:
    service = FakeSetupService()
    client = _build_client(service=service)
    response = client.post("/setup/folder", data={"folder_path": "~/Documents"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/setup/indexing"
    assert service.saved_sources == ["~/Documents"]
    assert service.start_index_calls == 1


# ---------------------------------------------------------------------------
# Indexing progress
# ---------------------------------------------------------------------------


def test_indexing_progress_advances_then_redirects() -> None:
    service = FakeSetupService(chunks_total=100, chunks_per_tick=50)
    client = _build_client(service=service)
    client.post("/setup/folder", data={"folder_path": "~/Documents"}, follow_redirects=False)

    first = client.get("/setup/indexing/progress")
    assert "50%" in first.text
    assert "in progress" in first.text
    assert "HX-Redirect" not in first.headers

    second = client.get("/setup/indexing/progress")
    assert "100%" in second.text
    assert "Indexing complete" in second.text
    assert second.headers["HX-Redirect"] == "/setup/first-search"


def test_indexing_progress_with_unknown_total_shows_zero_percent() -> None:
    pending = IndexStatus(running=True, done=False, chunks_done=0, chunks_total=0, error=None)
    service = FakeSetupService(index_statuses=(pending,))
    client = _build_client(service=service)
    response = client.get("/setup/indexing/progress")
    assert "0%" in response.text
    assert "HX-Redirect" not in response.headers


def test_indexing_error_renders_guided_error_without_redirect() -> None:
    service = FakeSetupService(index_error="Indexing stopped: provider rejected the embed request.")
    client = _build_client(service=service)
    response = client.get("/setup/indexing/progress")
    assert "Indexing stopped" in response.text
    assert "Fix" in response.text
    assert "HX-Redirect" not in response.headers


def test_indexing_screen_polls_the_progress_endpoint() -> None:
    client = _build_client()
    response = client.get("/setup/indexing")
    assert "/setup/indexing/progress" in response.text
    assert "every 1s" in response.text


# ---------------------------------------------------------------------------
# First search
# ---------------------------------------------------------------------------


def test_first_search_screen_offers_suggested_queries() -> None:
    client = _build_client()
    response = client.get("/setup/first-search")
    assert response.status_code == 200
    assert "Try your first search" in response.text
    assert "kx-query-suggestion" in response.text


def test_search_returns_result_cards() -> None:
    service = FakeSetupService()
    client = _build_client(service=service)
    response = client.post("/setup/search", data={"query": "project kickoff"})
    assert response.status_code == 200
    assert "Project kickoff notes" in response.text
    assert "92% match" in response.text
    assert "notes/kickoff.md" in response.text
    assert service.search_queries == ["project kickoff"]


# ---------------------------------------------------------------------------
# Connect agent
# ---------------------------------------------------------------------------


def test_connect_agent_screen_shows_mcp_url_and_snippets() -> None:
    service = FakeSetupService(mcp_url="http://127.0.0.1:8765/mcp")
    client = _build_client(service=service)
    response = client.get("/setup/connect-agent")
    assert response.status_code == 200
    assert "http://127.0.0.1:8765/mcp" in response.text
    assert "Claude Code" in response.text
    assert "mcpServers" in response.text
    assert "Verify connection" in response.text


def test_connect_agent_verify_reports_tool_count() -> None:
    service = FakeSetupService(tools_count=12)
    client = _build_client(service=service)
    response = client.post("/setup/connect-agent/verify")
    assert "Agent connected" in response.text
    assert "12 tools available" in response.text


def test_connect_agent_verify_failure_renders_guided_error() -> None:
    service = FakeSetupService(handshake_ok=False)
    client = _build_client(service=service)
    response = client.post("/setup/connect-agent/verify")
    assert "No agent handshake observed" in response.text
    assert "Fix" in response.text


# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------


def test_done_screen_celebrates_indexed_chunks() -> None:
    service = FakeSetupService(chunks_total=100, chunks_per_tick=100)
    client = _build_client(service=service)
    client.post("/setup/folder", data={"folder_path": "~/Documents"}, follow_redirects=False)
    client.get("/setup/indexing/progress")
    response = client.get("/setup/done")
    assert response.status_code == 200
    assert "Your knowledge is ready" in response.text
    assert "100" in response.text
    assert "chunks indexed" in response.text


# ---------------------------------------------------------------------------
# Operator token guard
# ---------------------------------------------------------------------------


def test_remote_request_without_configured_token_is_refused() -> None:
    client = _build_client(secrets=FakeSecretsLoader(), client_addr=("203.0.113.7", 4242))
    response = client.get("/setup/provider")
    assert response.status_code == 403
    assert "no operator token is configured" in response.text


def test_remote_request_requires_matching_token() -> None:
    secrets = FakeSecretsLoader(values={_OPERATOR_TOKEN_IDENTITY: "fake-operator-token"})
    client = _build_client(secrets=secrets, client_addr=("203.0.113.7", 4242))

    refused = client.get("/setup/provider")
    assert refused.status_code == 403
    assert "requires a valid operator token" in refused.text

    wrong = client.get("/setup/provider", headers={_TOKEN_HEADER: "wrong-token"})
    assert wrong.status_code == 403

    allowed = client.get("/setup/provider", headers={_TOKEN_HEADER: "fake-operator-token"})
    assert allowed.status_code == 200
    assert "Choose an AI provider" in allowed.text


def test_token_refusal_never_echoes_the_provided_token() -> None:
    secrets = FakeSecretsLoader(values={_OPERATOR_TOKEN_IDENTITY: "fake-operator-token"})
    client = _build_client(secrets=secrets, client_addr=("203.0.113.7", 4242))
    response = client.get("/setup/provider", headers={_TOKEN_HEADER: "attacker-guess-value"})
    assert response.status_code == 403
    assert "attacker-guess-value" not in response.text


def test_loopback_request_skips_the_token() -> None:
    client = _build_client(secrets=FakeSecretsLoader(), client_addr=_LOOPBACK)
    response = client.get("/setup/provider")
    assert response.status_code == 200
    assert "Choose an AI provider" in response.text


def test_unknown_client_address_fails_closed() -> None:
    """A request with no peer address is treated as remote, not loopback."""
    service = FakeSetupService()
    app = build_mcp_app(
        FakeMcpTransportServer(),
        setup_service_factory=lambda: service,
        setup_secrets=FakeSecretsLoader(),
        setup_wizard_enabled=lambda: True,
    )
    client = TestClient(app, client=None)  # type: ignore[arg-type]  # F3 rationale: TestClient accepts None to strip the peer address; the stub types only the tuple form.
    assert client.get("/setup/provider").status_code == 403


def test_default_secrets_seam_serves_loopback_without_config() -> None:
    """No injected secrets resolver → the production loader is the seam
    default; loopback requests never consult it so the wizard still serves."""
    service = FakeSetupService()
    app = build_mcp_app(
        FakeMcpTransportServer(),
        setup_service_factory=lambda: service,
        setup_wizard_enabled=lambda: True,
    )
    client = TestClient(app, client=_LOOPBACK)
    response = client.get("/setup/provider")
    assert response.status_code == 200
    assert "Choose an AI provider" in response.text


# ---------------------------------------------------------------------------
# Production default factory + cold-start bypass
# ---------------------------------------------------------------------------


def test_flag_on_with_production_default_factory_serves_the_wizard() -> None:
    """The production ``build_setup_service`` backend now exists — the
    default factory must serve the welcome screen, not the pre-backend
    503 stub. Construction is side-effect free (seams resolve lazily on
    first use), so rendering the welcome screen touches nothing real."""
    client = _build_client(use_default_factory=True)
    response = client.get("/setup", follow_redirects=True)
    assert response.status_code == 200
    assert "Welcome to kairix" in response.text


def test_wizard_bypasses_the_cold_start_gate() -> None:
    client = _build_client(readiness_check=lambda: False)
    # The wizard answers during warm-up — it exists for first-boot operators.
    welcome = client.get("/setup", follow_redirects=True)
    assert welcome.status_code == 200
    assert "Welcome to kairix" in welcome.text
    # Everything else still gets the structured cold-start 503.
    assert client.get("/mcp").status_code == 503
