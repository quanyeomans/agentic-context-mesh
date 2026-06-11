"""Unit tests for the wizard's source-OAuth building blocks (#489).

Covers ``kairix.platform.setup.source_oauth`` through its public
surface: the wizard-origin callback listener, the state-carrying
authorize-URL builders, the flow factory, canonical secret-name
derivation, unit discovery (with injected fake clients — no HTTP), and
the ``topology_v2`` config emission.
"""

from __future__ import annotations

import json
import threading

import pytest

from kairix.connect.protocols import (
    CallbackDeniedError,
    CallbackListener,
    CallbackTimeoutError,
    CapturedTokens,
    ClientCredentials,
)
from kairix.platform.setup.service import SourceUnit
from kairix.platform.setup.source_oauth import (
    OAUTH_CALLBACK_PATH,
    CapturingBrowser,
    SourceFlowRequest,
    WizardCallbackListener,
    build_source_flow,
    discover_source_units_live,
    google_authorize_url,
    slack_authorize_url,
    source_secret_leaves,
    topology_updates_for_source,
    write_secret_material,
)

pytestmark = pytest.mark.unit

_CLIENT = ClientCredentials(client_id="fake-client-id", client_secret="fake-client-secret")  # pragma: allowlist secret
_SLACK_TOKENS = CapturedTokens(
    refresh_token="",
    access_token="",
    token_uri="https://slack.test/token",
    bot_token="xoxb-fake",
)
_GOOGLE_TOKENS = CapturedTokens(
    refresh_token="fake-refresh",
    access_token="fake-access",
    token_uri="https://google.test/token",
)
_GITHUB_TOKENS = CapturedTokens(
    refresh_token="",
    access_token="ghs_fake",
    token_uri="https://github.test/token",
    metadata={"installation-id": "12345"},
)


# ---------------------------------------------------------------------------
# WizardCallbackListener
# ---------------------------------------------------------------------------


def test_listener_redirect_uri_derives_from_origin() -> None:
    listener = WizardCallbackListener(origin="http://localhost:8080/", expected_state="n1")
    assert listener.redirect_uri == f"http://localhost:8080{OAUTH_CALLBACK_PATH}"


def test_listener_satisfies_callback_listener_protocol() -> None:
    listener = WizardCallbackListener(origin="http://localhost:8080", expected_state=None)
    assert isinstance(listener, CallbackListener)


def test_listener_delivers_code_state_and_params() -> None:
    listener = WizardCallbackListener(origin="http://localhost:8080", expected_state="n1")
    listener.deliver({"code": "auth-code-1", "state": "n1", "extra": "v"})
    result = listener.wait_for_callback(timeout_s=1.0)
    assert result.code == "auth-code-1"
    assert result.state == "n1"
    assert result.params["extra"] == "v"


def test_listener_uses_installation_id_when_no_code() -> None:
    """The GitHub App install redirect carries installation_id, not code."""
    listener = WizardCallbackListener(origin="http://localhost:8080", expected_state=None)
    listener.deliver({"installation_id": "777", "setup_action": "install"})
    result = listener.wait_for_callback(timeout_s=1.0)
    assert result.code == "777"
    assert result.params["setup_action"] == "install"


def test_listener_denied_on_access_denied_param() -> None:
    listener = WizardCallbackListener(origin="http://localhost:8080", expected_state="n1")
    listener.deliver({"error": "access_denied"})
    with pytest.raises(CallbackDeniedError, match="cancelled"):
        listener.wait_for_callback(timeout_s=1.0)


def test_listener_denied_on_other_provider_error() -> None:
    listener = WizardCallbackListener(origin="http://localhost:8080", expected_state="n1")
    listener.deliver({"error": "redirect_uri_mismatch"})
    with pytest.raises(CallbackDeniedError, match="redirect_uri_mismatch"):
        listener.wait_for_callback(timeout_s=1.0)


def test_listener_denied_when_no_code_arrives() -> None:
    listener = WizardCallbackListener(origin="http://localhost:8080", expected_state="n1")
    listener.deliver({"state": "n1"})
    with pytest.raises(CallbackDeniedError, match="no authorization code"):
        listener.wait_for_callback(timeout_s=1.0)


def test_listener_times_out_with_guidance() -> None:
    listener = WizardCallbackListener(origin="http://localhost:8080", expected_state="n1")
    with pytest.raises(CallbackTimeoutError, match="fix:"):
        listener.wait_for_callback(timeout_s=0.01)


def test_listener_close_cancels_a_pending_wait_and_is_idempotent() -> None:
    listener = WizardCallbackListener(origin="http://localhost:8080", expected_state="n1")
    raised: list[BaseException] = []

    def waiter() -> None:
        try:
            listener.wait_for_callback(timeout_s=5.0)
        except CallbackTimeoutError as exc:
            raised.append(exc)

    thread = threading.Thread(target=waiter, daemon=True)
    thread.start()
    listener.close()
    listener.close()  # idempotent
    thread.join(timeout=5.0)
    assert len(raised) == 1
    assert "cancelled" in str(raised[0])


# ---------------------------------------------------------------------------
# CapturingBrowser + authorize-URL builders
# ---------------------------------------------------------------------------


def test_capturing_browser_records_instead_of_opening() -> None:
    browser = CapturingBrowser()
    assert browser.authorize_url is None
    assert browser.open("https://provider.test/consent") is True
    assert browser.authorize_url == "https://provider.test/consent"


def test_slack_authorize_url_carries_state_and_redirect() -> None:
    url = slack_authorize_url(_CLIENT, "http://localhost:8080/setup/oauth/callback", ("channels:read",), state="n-1")
    assert url.startswith("https://slack.com/oauth/v2/authorize?")
    assert "state=n-1" in url
    assert "redirect_uri=http%3A%2F%2Flocalhost%3A8080%2Fsetup%2Foauth%2Fcallback" in url
    assert "scope=channels%3Aread" in url


def test_google_authorize_url_carries_state_offline_and_consent() -> None:
    redirect = "http://localhost:8080/setup/oauth/callback"
    url = google_authorize_url(_CLIENT, redirect, ("scope.a", "scope.b"), state="n-2")
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "state=n-2" in url
    assert "access_type=offline" in url
    assert "prompt=consent" in url
    assert "scope=scope.a+scope.b" in url


# ---------------------------------------------------------------------------
# build_source_flow
# ---------------------------------------------------------------------------


def test_build_source_flow_slack_builds_state_carrying_flow() -> None:
    browser = CapturingBrowser()
    flow = build_source_flow(
        SourceFlowRequest(
            provider="slack",
            fields={"workspace": "alpha", "client_id": "id-1", "client_secret": "sec-1"},  # pragma: allowlist secret
            nonce="nonce-1",
            browser=browser,
        )
    )
    assert flow.service_area == "slack"
    assert flow.workspace == "alpha"


def test_build_source_flow_google_accepts_pasted_client_secret_json() -> None:
    pasted = json.dumps({"installed": {"client_id": "gid", "client_secret": "gsec"}})  # pragma: allowlist secret
    flow = build_source_flow(
        SourceFlowRequest(
            provider="gmail",
            fields={"client_secret_json": pasted},
            nonce="nonce-2",
            browser=CapturingBrowser(),
        )
    )
    assert flow.service_area == "gmail"
    credentials = flow.discover_client_credentials()
    assert credentials.client_id == "gid"


def test_build_source_flow_google_requires_credential_material() -> None:
    with pytest.raises(ValueError, match=r"client_secret\.json"):
        build_source_flow(SourceFlowRequest(provider="google-drive", fields={}, nonce="n", browser=CapturingBrowser()))


def test_build_source_flow_github_accepts_pasted_pem(tmp_path: object) -> None:
    pem = "-----BEGIN RSA PRIVATE KEY-----\nfakekeybody\n-----END RSA PRIVATE KEY-----\n"
    flow = build_source_flow(
        SourceFlowRequest(
            provider="github",
            fields={"app_id": "99", "private_key_pem": pem, "app_slug": "agent-alpha-app"},
            nonce="nonce-3",
            browser=CapturingBrowser(),
        )
    )
    assert flow.service_area == "github"
    credentials = flow.discover_client_credentials()
    assert credentials.client_id == "99"
    assert "PRIVATE KEY" in credentials.client_secret


def test_build_source_flow_rejects_unknown_provider() -> None:
    with pytest.raises(ValueError, match="fix:"):
        build_source_flow(SourceFlowRequest(provider="carrier-pigeon", fields={}, nonce="n", browser=None))


def test_write_secret_material_is_owner_only() -> None:
    path = write_secret_material("content", suffix=".json")
    try:
        assert path.read_text(encoding="utf-8") == "content"
        assert (path.stat().st_mode & 0o777) == 0o600
    finally:
        path.unlink()


# ---------------------------------------------------------------------------
# source_secret_leaves — canonical names only, never values
# ---------------------------------------------------------------------------


def test_slack_leaves_use_workspace_instance_slot() -> None:
    names = [name for name, _ in source_secret_leaves("slack", "alpha", _CLIENT, _SLACK_TOKENS)]
    assert names == [
        "kairix-connector-slack-alpha-client-id",
        "kairix-connector-slack-alpha-client-secret",
        "kairix-connector-slack-alpha-bot-token",
    ]


def test_google_leaves_match_the_connector_resolver_names() -> None:
    names = [name for name, _ in source_secret_leaves("gmail", None, _CLIENT, _GOOGLE_TOKENS)]
    assert names == [
        "kairix-connector-gmail-client-id",
        "kairix-connector-gmail-client-secret",
        "kairix-connector-gmail-refresh-token",
        "kairix-connector-gmail-access-token",
    ]


def test_github_leaves_use_the_app_triple_not_client_pairs() -> None:
    """The GitHub connector resolver reads app-id / app-private-key /
    installation-id — NOT client-id/client-secret (the App flow
    repurposes those slots)."""
    names = [name for name, _ in source_secret_leaves("github", None, _CLIENT, _GITHUB_TOKENS)]
    assert names == [
        "kairix-connector-github-app-id",
        "kairix-connector-github-app-private-key",
        "kairix-connector-github-installation-id",
    ]


# ---------------------------------------------------------------------------
# discover_source_units_live — injected fake clients, no HTTP
# ---------------------------------------------------------------------------


class _FakeSlackChannel:
    def __init__(self, channel_id: str, name: str, kind: str, *, archived: bool = False, member: bool = True) -> None:
        self.channel_id = channel_id
        self.name = name
        self.kind = kind
        self.is_archived = archived
        self.is_member = member


class _FakeSlackWeb:
    def __init__(self, channels: list[_FakeSlackChannel]) -> None:
        self._channels = channels
        self.requested_types: tuple[str, ...] = ()

    def conversations_list(self, *, types: tuple[str, ...]) -> list[_FakeSlackChannel]:
        self.requested_types = tuple(types)
        return self._channels


class _FakeRepo:
    def __init__(self, full_name: str, *, archived: bool = False) -> None:
        self.full_name = full_name
        self.default_branch = "main"
        self.visibility = "private"
        self.archived = archived


class _FakeGitHubApi:
    def __init__(self, repos: list[_FakeRepo]) -> None:
        self._repos = repos

    def list_installation_repositories(self) -> tuple[_FakeRepo, ...]:
        return tuple(self._repos)


def test_slack_discovery_maps_channels_and_skips_archived() -> None:
    web = _FakeSlackWeb(
        [
            _FakeSlackChannel("C1", "general", "public_channel"),
            _FakeSlackChannel("C2", "old-stuff", "public_channel", archived=True),
            _FakeSlackChannel("C3", "leads", "private_channel", member=False),
        ]
    )
    units = discover_source_units_live("slack", _CLIENT, _SLACK_TOKENS, slack_client_factory=lambda token: web)
    assert [u.unit_id for u in units] == ["C1", "C3"]
    assert units[0].name == "#general"
    assert "private channel" in units[1].detail
    assert "invite the app" in units[1].detail
    assert web.requested_types == ("public_channel", "private_channel")


def test_github_discovery_maps_repos_with_visibility_detail() -> None:
    api = _FakeGitHubApi([_FakeRepo("org/alpha"), _FakeRepo("org/beta", archived=True)])
    units = discover_source_units_live(
        "github", _CLIENT, _GITHUB_TOKENS, github_client_factory=lambda client, tokens: api
    )
    assert [u.unit_id for u in units] == ["org/alpha", "org/beta"]
    assert "private" in units[0].detail
    assert "archived" in units[1].detail


def test_google_areas_have_no_pickable_units() -> None:
    assert discover_source_units_live("google-drive", _CLIENT, _GOOGLE_TOKENS) == ()
    assert discover_source_units_live("gmail", _CLIENT, _GOOGLE_TOKENS) == ()
    assert discover_source_units_live("google-calendar", _CLIENT, _GOOGLE_TOKENS) == ()


# ---------------------------------------------------------------------------
# topology_updates_for_source
# ---------------------------------------------------------------------------


def test_slack_topology_emits_per_channel_path_filters() -> None:
    updates = topology_updates_for_source("slack", "alpha", ("C1", "C2"), {})
    topology = updates["topology_v2"]
    assert topology["connectors"][0]["kind"] == "slack"
    assert topology["connectors"][0]["connector_specific_config"] == {"workspace": "alpha"}
    # pragma: allowlist nextline secret — logical secret NAME, not a value
    assert topology["credentials"][0]["secret_name"] == "connector-slack-alpha"
    assert topology["cc_pairs"][0]["connector"] == "slack-alpha-conn"
    filters = [source["path_filter"] for source in topology["collections"][0]["sources"]]
    assert filters == ["slack://channel/C1/*", "slack://channel/C2/*"]


def test_github_topology_emits_repos_allowlist() -> None:
    updates = topology_updates_for_source("github", "", ("org/alpha", "org/beta"), {})
    topology = updates["topology_v2"]
    connector = topology["connectors"][0]
    assert connector["kind"] == "github"
    assert connector["connector_specific_config"]["repos_allowlist"] == ["org/alpha", "org/beta"]
    assert topology["credentials"][0]["kind"] == "github_dual_path"


def test_gmail_topology_carries_the_mailbox() -> None:
    updates = topology_updates_for_source("gmail", "agent-alpha@example.com", (), {})
    connector = updates["topology_v2"]["connectors"][0]
    assert connector["kind"] == "gmail"
    assert connector["connector_specific_config"] == {"user_email": "agent-alpha@example.com"}


def test_google_drive_topology_defaults_the_corpus_label() -> None:
    updates = topology_updates_for_source("google-drive", "", (), {})
    connector = updates["topology_v2"]["connectors"][0]
    assert connector["kind"] == "google_drive"
    assert connector["connector_specific_config"] == {"corpora": ["my-drive"]}


def test_calendar_topology_defaults_to_primary() -> None:
    updates = topology_updates_for_source("google-calendar", "", (), {})
    connector = updates["topology_v2"]["connectors"][0]
    assert connector["connector_specific_config"] == {"calendar_id": "primary"}


def test_topology_upsert_preserves_other_sources_and_replaces_same_id() -> None:
    first = topology_updates_for_source("slack", "alpha", ("C1",), {})
    second = topology_updates_for_source("github", "", ("org/alpha",), first)
    topology = second["topology_v2"]
    kinds = {connector["kind"] for connector in topology["connectors"]}
    assert kinds == {"slack", "github"}
    # Re-saving slack with new picks replaces its rows, no duplicates.
    third = topology_updates_for_source("slack", "alpha", ("C9",), second)
    slack_connectors = [c for c in third["topology_v2"]["connectors"] if c["kind"] == "slack"]
    assert len(slack_connectors) == 1
    slack_collections = [c for c in third["topology_v2"]["collections"] if c["name"] == "slack-alpha"]
    assert slack_collections[0]["sources"][0]["path_filter"] == "slack://channel/C9/*"


def test_emitted_topology_parses_under_the_canonical_parser() -> None:
    """The wizard's emission must round-trip through the same parser the
    worker boots with — proves the shapes are real, not wishful."""
    from kairix.config.topology_v2 import parse_topology_v2

    updates = topology_updates_for_source("slack", "alpha", ("C1",), {})
    updates = topology_updates_for_source("github", "", ("org/alpha",), updates)
    parsed = parse_topology_v2(updates)
    assert {c.kind for c in parsed.connectors} == {"slack", "github"}
    assert {p.name for p in parsed.cc_pairs} == {"slack-alpha", "github-app"}
    assert {c.name for c in parsed.collections} == {"slack-alpha", "github-repos"}


def test_fake_oauth2_flow_satisfies_the_flow_protocol() -> None:
    from kairix.connect.protocols import OAuth2Flow
    from tests.fakes import FakeOAuth2Flow

    assert isinstance(FakeOAuth2Flow(), OAuth2Flow)


def test_source_unit_is_frozen() -> None:
    unit = SourceUnit(unit_id="C1", name="#general")
    with pytest.raises(AttributeError):
        unit.name = "#other"  # type: ignore[misc]  # F3 rationale: intentional frozen-dataclass mutation probe.
