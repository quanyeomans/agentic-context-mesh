"""Outcome tests for the wizard's OAuth source-connect screens (#489).

Same composition shape as ``test_setup_wizard_web_routes.py``: the real
Starlette app through ``build_mcp_app`` with the canonical fakes from
``tests/fakes.py`` injected through the public seams. Assertions are on
rendered HTML / headers (F30 spirit), never on status codes alone.
"""

from __future__ import annotations

import pytest

# starlette ships via the optional [agents] extra (transitive dep of mcp);
# CI's base-deps stages must skip rather than fail on the missing import.
starlette = pytest.importorskip("starlette")
from starlette.testclient import TestClient  # noqa: E402

from kairix.agents.mcp.transport import build_mcp_app  # noqa: E402
from kairix.platform.setup.service import (  # noqa: E402
    SetupService,
    SourceAuthStatus,
    SourceUnit,
)
from tests.fakes import (  # noqa: E402
    FakeMcpTransportServer,
    FakeSecretsLoader,
    FakeSetupService,
)

pytestmark = pytest.mark.unit

_LOOPBACK = ("127.0.0.1", 9999)
_REMOTE = ("203.0.113.7", 4242)
# Fixture credentials for the fake service, not real values.
_SLACK_FORM = {
    "provider": "slack",
    "workspace": "alpha",
    "client_id": "fake-id",
    "client_secret": "fake-secret-for-tests",  # pragma: allowlist secret
}
_CONSENT_URL = "https://provider.test/consent"
_CONSENT = SourceAuthStatus(provider="slack", phase="consent", authorize_url=_CONSENT_URL, error=None)
_DONE = SourceAuthStatus(provider="slack", phase="done", authorize_url=_CONSENT_URL, error=None)


def _build_client(
    *,
    service: SetupService | None = None,
    client_addr: tuple[str, int] = _LOOPBACK,
) -> TestClient:
    """Compose the app through the production composer with fakes."""
    resolved_service = service if service is not None else FakeSetupService()
    app = build_mcp_app(
        FakeMcpTransportServer(),
        setup_service_factory=lambda: resolved_service,
        setup_secrets=FakeSecretsLoader(),
        setup_wizard_enabled=lambda: True,
    )
    return TestClient(app, client=client_addr)


# ---------------------------------------------------------------------------
# Source cards
# ---------------------------------------------------------------------------


def test_source_step_offers_folder_and_oauth_cards() -> None:
    client = _build_client()
    response = client.get("/setup/source")
    assert response.status_code == 200
    for label in ("Folder", "Slack", "GitHub", "Google Drive", "Gmail", "Google Calendar"):
        assert label in response.text
    # The folder card routes to the existing folder screen.
    assert "/setup/folder" in response.text
    assert "/setup/source/connect?provider=slack" in response.text


# ---------------------------------------------------------------------------
# Connect form
# ---------------------------------------------------------------------------


def test_connect_form_shows_the_origin_derived_redirect_uri() -> None:
    client = _build_client()
    response = client.get("/setup/source/connect", params={"provider": "slack"})
    assert response.status_code == 200
    # TestClient's origin is http://testserver — the displayed redirect
    # URI must derive from the LIVE request origin, never a hardcoded
    # localhost.
    assert "http://testserver/setup/oauth/callback" in response.text
    assert "Redirect URL to register" in response.text


def test_connect_form_fields_per_provider() -> None:
    client = _build_client()
    slack = client.get("/setup/source/connect", params={"provider": "slack"}).text
    assert 'name="workspace"' in slack
    assert 'name="client_id"' in slack
    assert 'name="client_secret"' in slack
    github = client.get("/setup/source/connect", params={"provider": "github"}).text
    assert 'name="app_id"' in github
    assert 'name="private_key_pem"' in github
    google = client.get("/setup/source/connect", params={"provider": "gmail"}).text
    assert 'name="client_secret_json"' in google
    assert "Google Cloud console" in google


def test_connect_form_unknown_provider_returns_to_the_cards() -> None:
    client = _build_client()
    response = client.get("/setup/source/connect", params={"provider": "carrier-pigeon"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/setup/source"


def test_connect_start_records_fields_and_origin_then_waits() -> None:
    service = FakeSetupService()
    client = _build_client(service=service)
    response = client.post("/setup/source/connect", data=_SLACK_FORM, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/setup/source/wait?provider=slack"
    provider, fields, origin = service.source_auth_starts[0]
    assert provider == "slack"
    assert fields["workspace"] == "alpha"
    assert origin == "http://testserver"


def test_connect_start_failure_rerenders_with_guidance() -> None:
    service = FakeSetupService(
        source_auth_start_error="The workspace name is required. fix: enter it. next: connect again.",
    )
    client = _build_client(service=service)
    response = client.post("/setup/source/connect", data=_SLACK_FORM)
    assert response.status_code == 200
    assert "workspace name is required" in response.text
    assert "fix:" in response.text
    # The redirect URI stays visible so the operator can still register it.
    assert "http://testserver/setup/oauth/callback" in response.text


# ---------------------------------------------------------------------------
# Wait screen + status poll
# ---------------------------------------------------------------------------


def test_wait_screen_polls_the_status_endpoint() -> None:
    client = _build_client()
    response = client.get("/setup/source/wait", params={"provider": "slack"})
    assert response.status_code == 200
    assert "/setup/source/auth-status" in response.text


def test_status_poll_sends_the_browser_to_consent_then_picker() -> None:
    service = FakeSetupService(source_auth_statuses=(_CONSENT, _DONE))
    client = _build_client(service=service)
    first = client.get("/setup/source/auth-status")
    assert first.headers["HX-Redirect"] == _CONSENT_URL
    second = client.get("/setup/source/auth-status")
    assert second.headers["HX-Redirect"] == "/setup/source/picker?provider=slack"


def test_status_poll_renders_failure_guidance() -> None:
    failed = SourceAuthStatus(
        provider="slack",
        phase="failed",
        authorize_url=None,
        error="The sign-in was cancelled on the provider's consent screen. fix: approve it. next: retry.",
    )
    service = FakeSetupService(source_auth_statuses=(failed,))
    client = _build_client(service=service)
    response = client.get("/setup/source/auth-status")
    assert response.status_code == 200
    assert "HX-Redirect" not in response.headers
    assert "cancelled" in response.text
    assert "Fix" in response.text


# ---------------------------------------------------------------------------
# Callback route — the wizard-origin redirect target
# ---------------------------------------------------------------------------


def test_callback_with_no_pending_flow_is_rejected_with_guidance() -> None:
    service = FakeSetupService(callback_ok=False)
    client = _build_client(service=service)
    response = client.get("/setup/oauth/callback", params={"code": "auth-1"}, follow_redirects=False)
    assert response.status_code == 409
    assert "No source connection is waiting" in response.text
    assert "fix:" in response.text


def test_callback_delivers_state_and_params_then_returns_to_wait() -> None:
    service = FakeSetupService()
    client = _build_client(service=service)
    response = client.get(
        "/setup/oauth/callback",
        params={"code": "auth-1", "state": "nonce-1"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/setup/source/wait"
    state, params = service.callback_deliveries[0]
    assert state == "nonce-1"
    assert params["code"] == "auth-1"


def test_callback_is_exempt_from_the_operator_token_guard() -> None:
    """A provider redirect cannot carry the operator-token header, so
    the callback path passes the guard; every other wizard path keeps
    requiring it for non-loopback clients."""
    service = FakeSetupService()
    client = _build_client(service=service, client_addr=_REMOTE)
    callback = client.get("/setup/oauth/callback", params={"code": "auth-1"}, follow_redirects=False)
    assert callback.status_code == 303
    guarded = client.get("/setup/source", follow_redirects=False)
    assert guarded.status_code == 403


# ---------------------------------------------------------------------------
# Picker + save
# ---------------------------------------------------------------------------


def test_picker_renders_units_as_checkboxes() -> None:
    service = FakeSetupService(
        source_units=(
            SourceUnit(unit_id="C001", name="#general", detail="public channel"),
            SourceUnit(unit_id="C002", name="#engineering", detail="private channel"),
        )
    )
    client = _build_client(service=service)
    response = client.get("/setup/source/picker", params={"provider": "slack"})
    assert response.status_code == 200
    assert "#general" in response.text
    assert "#engineering" in response.text
    assert 'name="unit" value="C001"' in response.text


def test_picker_confirm_screen_for_unpickable_sources() -> None:
    service = FakeSetupService(
        source_units=(),
        source_units_pickable=False,
        source_units_note="kairix will index email from this mailbox. Enter the mailbox address to confirm.",
    )
    client = _build_client(service=service)
    response = client.get("/setup/source/picker", params={"provider": "gmail"})
    assert "mailbox" in response.text
    assert 'name="instance"' in response.text
    assert 'name="unit"' not in response.text


def test_picker_renders_discovery_errors_with_guidance() -> None:
    service = FakeSetupService(
        source_units_error="Could not list what this source offers: 429. fix: retry. next: reload.",
    )
    client = _build_client(service=service)
    response = client.get("/setup/source/picker", params={"provider": "slack"})
    assert "Could not list" in response.text
    assert "fix:" in response.text


def test_save_posts_picks_and_shows_the_pre_spend_summary() -> None:
    service = FakeSetupService()
    client = _build_client(service=service)
    response = client.post(
        "/setup/source/save",
        data={"provider": "slack", "unit": ["C001", "C002"]},
    )
    assert response.status_code == 200
    assert "2 channels selected" in response.text
    assert "nothing is downloaded until indexing runs" in response.text.lower()
    assert service.saved_oauth_sources == [("slack", "", ("C001", "C002"))]


def test_save_validation_reject_rerenders_the_picker() -> None:
    service = FakeSetupService(
        save_oauth_error="Nothing is selected yet. fix: tick at least one item to index. next: save again.",
    )
    client = _build_client(service=service)
    response = client.post("/setup/source/save", data={"provider": "slack"})
    assert response.status_code == 200
    assert "Nothing is selected yet" in response.text
    assert "Pick what to index" in response.text


def test_save_on_read_only_config_shows_the_rescue_banner() -> None:
    read_only = OSError(30, "Read-only file system", "/etc/kairix/kairix.config.yaml")
    service = FakeSetupService(save_oauth_raises=read_only)
    client = _build_client(service=service)
    response = client.post("/setup/source/save", data={"provider": "slack", "unit": ["C001"]})
    assert response.status_code == 200
    assert "read-only" in response.text
    assert "KAIRIX_CONFIG_OVERLAY_PATH" in response.text
