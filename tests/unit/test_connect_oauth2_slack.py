"""Unit-level coverage for kairix.connect.oauth2.slack."""

from __future__ import annotations

from typing import Any

import pytest

from kairix.connect.oauth2.slack import (
    DEFAULT_SLACK_BOT_SCOPES,
    SLACK_AUTHORIZE_URL,
    SLACK_SERVICE_AREA,
    SLACK_TOKEN_URI,
    SlackOAuth2Flow,
)
from kairix.connect.protocols import CallbackResult, CapturedTokens, ClientCredentials, OAuth2Flow
from tests.fakes import FakeBrowserLauncher, FakeCallbackListener

pytestmark = pytest.mark.unit


def _flow(
    *,
    workspace: str = "alpha",
    client_id: str = "cid",
    client_secret: str = "csec",  # pragma: allowlist secret
    **kwargs: Any,
) -> SlackOAuth2Flow:
    """Construct a SlackOAuth2Flow for tests — defaults match BDD fixtures."""
    return SlackOAuth2Flow(
        workspace=workspace,
        client_id=client_id,
        client_secret=client_secret,
        **kwargs,
    )


def test_empty_workspace_raises_with_f21_hint() -> None:
    """An empty workspace string is the operator-correctable shape."""
    with pytest.raises(ValueError) as exc_info:
        SlackOAuth2Flow(workspace="", client_id="x", client_secret="y")
    msg = str(exc_info.value)
    assert "non-empty" in msg
    assert "fix:" in msg
    assert "next:" in msg
    assert "run:" in msg


def test_empty_client_id_raises_with_f21_hint() -> None:
    with pytest.raises(ValueError) as exc_info:
        SlackOAuth2Flow(workspace="alpha", client_id="", client_secret="csec")  # pragma: allowlist secret
    msg = str(exc_info.value)
    assert "client_id" in msg
    assert "fix:" in msg
    assert "api.slack.com" in msg


def test_empty_client_secret_raises_with_f21_hint() -> None:
    with pytest.raises(ValueError) as exc_info:
        SlackOAuth2Flow(workspace="alpha", client_id="cid", client_secret="")
    msg = str(exc_info.value)
    assert "client_secret" in msg
    assert "fix:" in msg
    assert "api.slack.com" in msg


def test_default_scopes_match_connector_oauth_surface() -> None:
    """The default scopes match the Slack connector's OAuth surface."""
    flow = _flow()
    assert flow.scopes == DEFAULT_SLACK_BOT_SCOPES
    # Pin specific load-bearing scopes so a refactor that drops one
    # surfaces here — not at the first 401 in production.
    assert "channels:history" in flow.scopes
    assert "users:read" in flow.scopes


def test_explicit_scopes_override() -> None:
    flow = _flow(scopes=("custom-scope",))
    assert flow.scopes == ("custom-scope",)


def test_service_area_is_slack() -> None:
    assert _flow().service_area == SLACK_SERVICE_AREA


def test_satisfies_oauth2_flow_protocol() -> None:
    """SlackOAuth2Flow satisfies the OAuth2Flow Protocol (F43)."""
    assert isinstance(_flow(), OAuth2Flow)


def test_discover_client_credentials_returns_typed_pair() -> None:
    flow = _flow(client_id="bdd-cid", client_secret="bdd-csec")  # pragma: allowlist secret
    creds = flow.discover_client_credentials()
    assert creds.client_id == "bdd-cid"
    assert creds.client_secret == "bdd-csec"  # pragma: allowlist secret


def test_authorize_url_contains_required_params() -> None:
    """The authorize URL carries client_id + redirect_uri + scopes."""
    browser = FakeBrowserLauncher()
    listener = FakeCallbackListener()
    flow = _flow(
        client_id="bdd-cid",
        browser=browser,
        token_exchanger=lambda _c, _code, _ru: CapturedTokens(
            refresh_token="",
            access_token="",
            token_uri=SLACK_TOKEN_URI,
            bot_token="xoxb-bdd",
        ),
    )
    flow.authorize(listener=listener)
    assert len(browser.opened) == 1
    url = browser.opened[0]
    assert url.startswith(SLACK_AUTHORIZE_URL)
    assert "client_id=bdd-cid" in url
    assert "redirect_uri=http%3A%2F%2F127.0.0.1%3A8080%2Foauth2callback" in url
    assert "channels%3Ahistory" in url  # URL-encoded comma-joined scopes


def test_authorize_url_builder_injection() -> None:
    """Custom ``authorize_url_builder`` overrides the default."""

    def builder(client: ClientCredentials, redirect_uri: str, scopes: tuple[str, ...]) -> str:
        return f"custom://slack?cid={client.client_id}&ru={redirect_uri}&n={len(scopes)}"

    browser = FakeBrowserLauncher()
    listener = FakeCallbackListener()
    flow = _flow(
        client_id="bdd-cid",
        browser=browser,
        authorize_url_builder=builder,
        token_exchanger=lambda _c, _code, _ru: CapturedTokens(
            refresh_token="",
            access_token="",
            token_uri=SLACK_TOKEN_URI,
            bot_token="xoxb-bdd",
        ),
    )
    flow.authorize(listener=listener)
    assert browser.opened == [
        f"custom://slack?cid=bdd-cid&ru=http://127.0.0.1:8080/oauth2callback&n={len(DEFAULT_SLACK_BOT_SCOPES)}",
    ]


def test_authorize_happy_path_with_injected_exchanger() -> None:
    """Full authorize flow with injected listener + browser + exchanger fakes."""
    browser = FakeBrowserLauncher()
    listener = FakeCallbackListener()

    def fake_exchanger(client: ClientCredentials, code: str, redirect_uri: str) -> CapturedTokens:
        assert client.client_id == "bdd-cid"
        assert code == "fake-code-001"
        assert redirect_uri == "http://127.0.0.1:8080/oauth2callback"
        return CapturedTokens(
            refresh_token="",
            access_token="",
            token_uri=SLACK_TOKEN_URI,
            bot_token=f"xoxb-from-{code}",
        )

    flow = _flow(
        client_id="bdd-cid",
        browser=browser,
        token_exchanger=fake_exchanger,
    )
    tokens = flow.authorize(listener=listener)
    assert tokens.bot_token == "xoxb-from-fake-code-001"
    assert tokens.refresh_token == ""  # Slack documented partial state
    assert tokens.access_token == ""  # bot_token carries the credential
    assert len(browser.opened) == 1


def test_default_authorize_url_shape_via_public_authorize() -> None:
    """The default authorize-URL builder emits Slack's OAuth v2 shape.

    Drives the default builder through the public ``SlackOAuth2Flow.authorize``
    surface — no private-import — so a refactor that drops the URL
    contract surfaces here. The browser fake records the URL the
    operator would have seen.
    """
    browser = FakeBrowserLauncher()
    # Use a listener with a non-default redirect_uri to confirm the URL
    # uses it verbatim (not a hardcoded port).
    listener = FakeCallbackListener(redirect_uri="http://127.0.0.1:9090/oauth2callback")
    flow = _flow(
        client_id="cid",
        scopes=("channels:history", "users:read"),
        browser=browser,
        token_exchanger=lambda _c, _code, _ru: CapturedTokens(
            refresh_token="",
            access_token="",
            token_uri=SLACK_TOKEN_URI,
            bot_token="xoxb-x",
        ),
    )
    flow.authorize(listener=listener)
    assert len(browser.opened) == 1
    url = browser.opened[0]
    assert url.startswith(SLACK_AUTHORIZE_URL)
    assert "client_id=cid" in url
    assert "redirect_uri=http%3A%2F%2F127.0.0.1%3A9090%2Foauth2callback" in url
    # Slack's OAuth v2 scopes are comma-joined (not space-joined like Google)
    assert "scope=channels%3Ahistory%2Cusers%3Aread" in url


def _ok_payload(*, token: str = "xoxb-live", team_id: str = "T_X", team_name: str = "Name") -> dict[str, Any]:
    """Synthetic Slack oauth.v2.access success response (mirrors live shape)."""
    return {
        "ok": True,
        "access_token": token,
        "scope": "channels:history,users:read",
        "team": {"id": team_id, "name": team_name},
    }


def test_authorize_default_exchange_happy_path_via_http_post() -> None:
    """SlackOAuth2Flow(http_post=fake) drives the default exchange happy path.

    Drives every parsing branch through the public
    ``SlackOAuth2Flow.authorize`` surface using the ``http_post=``
    injection seam — no private-import. Pins that the form payload
    sent to Slack carries the OAuth v2 shape and the captured bot_token
    + team metadata round-trip onto the flow instance.
    """
    captured: dict[str, Any] = {}

    def fake_post(url: str, form: dict[str, str]) -> dict[str, Any]:
        captured["url"] = url
        captured["form"] = form
        return _ok_payload(token="xoxb-live-token", team_id="T_ALPHA", team_name="Alpha")

    listener = FakeCallbackListener(callback=CallbackResult(code="ok-code", state=None))
    flow = _flow(http_post=fake_post)
    tokens = flow.authorize(listener=listener)
    assert tokens.bot_token == "xoxb-live-token"
    assert tokens.refresh_token == ""
    assert tokens.token_uri == SLACK_TOKEN_URI
    assert flow.team_id == "T_ALPHA"
    assert flow.team_name == "Alpha"
    # Confirm the form payload is the Slack OAuth v2 shape Slack expects.
    assert captured["url"] == SLACK_TOKEN_URI
    assert captured["form"]["client_id"] == "cid"
    assert captured["form"]["code"] == "ok-code"
    assert captured["form"]["redirect_uri"] == "http://127.0.0.1:8080/oauth2callback"


def test_authorize_default_exchange_raises_when_ok_false() -> None:
    """``ok: false`` surfaces a typed RuntimeError with F21 hint."""

    def fake_post(_url: str, _form: dict[str, str]) -> dict[str, Any]:
        return {"ok": False, "error": "invalid_code"}

    flow = _flow(http_post=fake_post)
    with pytest.raises(RuntimeError) as exc_info:
        flow.authorize(listener=FakeCallbackListener())
    msg = str(exc_info.value)
    # Must mention BOTH the load-bearing rationale (ok=false) AND the
    # error code so a regression that drops either is caught.
    assert "ok=false" in msg, f"expected 'ok=false' in error, got: {msg!r}"
    assert "invalid_code" in msg, f"expected 'invalid_code' in error, got: {msg!r}"
    assert "fix:" in msg and "next:" in msg and "run:" in msg, f"expected F21 markers in error, got: {msg!r}"


def test_authorize_default_exchange_raises_on_empty_access_token() -> None:
    """An empty access_token in Slack's ok-true response surfaces a typed error."""

    def fake_post(_url: str, _form: dict[str, str]) -> dict[str, Any]:
        return {"ok": True, "access_token": "", "team": {"id": "T1", "name": "n"}}

    flow = _flow(http_post=fake_post)
    with pytest.raises(RuntimeError) as exc_info:
        flow.authorize(listener=FakeCallbackListener())
    msg = str(exc_info.value)
    # Must mention BOTH the load-bearing rationale (empty access_token)
    # AND the remediation hint (Bot Token Scopes) so a regression that
    # drops either is caught.
    assert "empty access_token" in msg, f"expected 'empty access_token' in error, got: {msg!r}"
    assert "Bot Token Scopes" in msg, f"expected remediation hint in error, got: {msg!r}"
    assert "fix:" in msg and "next:" in msg and "run:" in msg, f"expected F21 markers in error, got: {msg!r}"


def test_authorize_default_exchange_handles_missing_team() -> None:
    """When Slack's response omits the team key, team_id + team_name stay empty."""

    def fake_post(_url: str, _form: dict[str, str]) -> dict[str, Any]:
        return {"ok": True, "access_token": "xoxb-x"}

    flow = _flow(http_post=fake_post)
    tokens = flow.authorize(listener=FakeCallbackListener())
    assert tokens.bot_token == "xoxb-x"
    assert flow.team_id == ""
    assert flow.team_name == ""


def test_constants_are_distinct_from_google() -> None:
    """Slack's token URI is distinct from Google's — guards copy-paste."""
    from kairix.connect.oauth2.google import GOOGLE_TOKEN_URI

    assert SLACK_TOKEN_URI != GOOGLE_TOKEN_URI
    assert "slack.com" in SLACK_TOKEN_URI
    assert "slack.com" in SLACK_AUTHORIZE_URL
