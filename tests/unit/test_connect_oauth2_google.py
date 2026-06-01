"""Unit-level coverage for kairix.connect.oauth2.google."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kairix.connect.oauth2.google import (
    CALENDAR_READONLY_SCOPE,
    DEFAULT_SCOPES_BY_AREA,
    DRIVE_READONLY_SCOPE,
    GMAIL_READONLY_SCOPE,
    GOOGLE_TOKEN_URI,
    GoogleOAuth2Flow,
)
from kairix.connect.protocols import CapturedTokens, ClientCredentials
from tests.fakes import FakeBrowserLauncher, FakeCallbackListener

pytestmark = pytest.mark.unit


def _write_client_secret(path: Path, *, top_key: str = "installed") -> None:
    payload = {
        top_key: {
            "client_id": "cid-test",
            "client_secret": "csec-test",  # pragma: allowlist secret
            "auth_uri": "https://accounts.google.com/o/oauth2/v2/auth",
            "token_uri": GOOGLE_TOKEN_URI,
        },
    }
    path.write_text(json.dumps(payload))


def _flow(path: Path, *, area: str = "gmail") -> GoogleOAuth2Flow:
    """Build a flow for the given path — drives through the public surface."""
    return GoogleOAuth2Flow(service_area=area, client_secret_path=path)


def test_parse_installed_shape(tmp_path: Path) -> None:
    p = tmp_path / "cs.json"
    _write_client_secret(p, top_key="installed")
    blob = _flow(p).discover_client_credentials()
    assert blob.client_id == "cid-test"
    assert blob.client_secret == "csec-test"  # pragma: allowlist secret


def test_parse_web_shape(tmp_path: Path) -> None:
    p = tmp_path / "cs.json"
    _write_client_secret(p, top_key="web")
    blob = _flow(p).discover_client_credentials()
    assert blob.client_id == "cid-test"


def test_missing_file_raises_with_f21_hint(tmp_path: Path) -> None:
    p = tmp_path / "missing.json"
    with pytest.raises(FileNotFoundError) as exc_info:
        _flow(p).discover_client_credentials()
    msg = str(exc_info.value)
    assert "fix:" in msg
    assert "next:" in msg
    assert "run:" in msg
    assert "GCP console" in msg


def test_malformed_json_raises(tmp_path: Path) -> None:
    p = tmp_path / "cs.json"
    p.write_text("{not json")
    with pytest.raises(ValueError, match="not valid JSON"):
        _flow(p).discover_client_credentials()


def test_missing_top_level_key_raises(tmp_path: Path) -> None:
    p = tmp_path / "cs.json"
    p.write_text(json.dumps({"some_other_key": {}}))
    with pytest.raises(ValueError, match="installed' or 'web"):
        _flow(p).discover_client_credentials()


def test_missing_client_id_in_section_raises(tmp_path: Path) -> None:
    p = tmp_path / "cs.json"
    p.write_text(json.dumps({"installed": {"client_secret": "only-secret"}}))  # pragma: allowlist secret
    with pytest.raises(ValueError, match="client_id"):
        _flow(p).discover_client_credentials()


def test_unknown_service_area_raises(tmp_path: Path) -> None:
    p = tmp_path / "cs.json"
    _write_client_secret(p)
    with pytest.raises(ValueError, match="unknown Google service_area"):
        GoogleOAuth2Flow(service_area="dropbox", client_secret_path=p)


def test_default_scopes_per_area(tmp_path: Path) -> None:
    p = tmp_path / "cs.json"
    _write_client_secret(p)
    for area, expected in DEFAULT_SCOPES_BY_AREA.items():
        flow = GoogleOAuth2Flow(service_area=area, client_secret_path=p)
        assert flow.scopes == expected
        assert flow.service_area == area


def test_explicit_scopes_override(tmp_path: Path) -> None:
    p = tmp_path / "cs.json"
    _write_client_secret(p)
    flow = GoogleOAuth2Flow(
        service_area="gmail",
        client_secret_path=p,
        scopes=("custom-scope",),
    )
    assert flow.scopes == ("custom-scope",)


def test_authorize_url_contains_required_params(tmp_path: Path) -> None:
    """Drive authorize URL construction through the public flow.authorize() path.

    Uses an injected browser that records the URL, and a stub token
    exchanger to skip the actual exchange — pins that the production
    authorize URL carries client_id + redirect_uri + access_type=offline +
    prompt=consent + the requested scope.
    """
    p = tmp_path / "cs.json"
    _write_client_secret(p)
    browser = FakeBrowserLauncher()
    listener = FakeCallbackListener()
    flow = GoogleOAuth2Flow(
        service_area="gmail",
        client_secret_path=p,
        browser=browser,
        token_exchanger=lambda _c, _code, _ru: CapturedTokens(
            refresh_token="rt",
            access_token="at",
            token_uri=GOOGLE_TOKEN_URI,
        ),
    )
    flow.authorize(listener=listener)
    assert len(browser.opened) == 1
    url = browser.opened[0]
    assert "client_id=cid-test" in url
    assert "redirect_uri=http%3A%2F%2F127.0.0.1%3A8080%2Foauth2callback" in url
    assert "access_type=offline" in url
    assert "prompt=consent" in url
    assert "gmail.readonly" in url


def test_discover_client_credentials_returns_typed_pair(tmp_path: Path) -> None:
    p = tmp_path / "cs.json"
    _write_client_secret(p)
    flow = GoogleOAuth2Flow(service_area="gmail", client_secret_path=p)
    client = flow.discover_client_credentials()
    assert client.client_id == "cid-test"
    assert client.client_secret == "csec-test"  # pragma: allowlist secret


def test_authorize_happy_path_with_injected_exchanger(tmp_path: Path) -> None:
    """Full authorize flow with injected listener + browser + exchanger fakes."""
    p = tmp_path / "cs.json"
    _write_client_secret(p)
    browser = FakeBrowserLauncher()
    listener = FakeCallbackListener()

    def fake_exchanger(client: ClientCredentials, code: str, redirect_uri: str) -> CapturedTokens:
        assert client.client_id == "cid-test"
        assert code == "fake-code-001"
        assert redirect_uri == "http://127.0.0.1:8080/oauth2callback"
        return CapturedTokens(
            refresh_token="captured-refresh",
            access_token="captured-access",
            token_uri=GOOGLE_TOKEN_URI,
        )

    flow = GoogleOAuth2Flow(
        service_area="gmail",
        client_secret_path=p,
        browser=browser,
        token_exchanger=fake_exchanger,
    )
    tokens = flow.authorize(listener=listener)
    assert tokens.refresh_token == "captured-refresh"
    assert tokens.access_token == "captured-access"
    # Browser was asked to open the authorize URL.
    assert len(browser.opened) == 1
    assert "client_id=cid-test" in browser.opened[0]


def test_authorize_url_builder_injection(tmp_path: Path) -> None:
    """Custom ``authorize_url_builder`` overrides the default."""
    p = tmp_path / "cs.json"
    _write_client_secret(p)

    def builder(client: ClientCredentials, redirect_uri: str, scopes: tuple[str, ...]) -> str:
        return f"custom://auth?cid={client.client_id}&scopes={','.join(scopes)}"

    browser = FakeBrowserLauncher()
    listener = FakeCallbackListener()
    flow = GoogleOAuth2Flow(
        service_area="google-drive",
        client_secret_path=p,
        browser=browser,
        authorize_url_builder=builder,
        token_exchanger=lambda _c, _code, _ru: CapturedTokens(
            refresh_token="rt",
            access_token="at",
            token_uri=GOOGLE_TOKEN_URI,
        ),
    )
    flow.authorize(listener=listener)
    assert browser.opened == [f"custom://auth?cid=cid-test&scopes={DRIVE_READONLY_SCOPE}"]


def test_scope_constants_are_distinct() -> None:
    """The three Google scopes are different — guards a copy-paste bug."""
    scopes = {GMAIL_READONLY_SCOPE, DRIVE_READONLY_SCOPE, CALENDAR_READONLY_SCOPE}
    assert len(scopes) == 3
    assert GOOGLE_TOKEN_URI.startswith("https://")
