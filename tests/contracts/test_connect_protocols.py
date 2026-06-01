"""Contract tests for kairix.connect Protocols (ADR-032 + F43 + F68).

Each Protocol gets:
  1. isinstance() conformance check against the real implementation.
  2. isinstance() conformance check against the fake from tests.fakes.
  3. One F68 failure-injection test per Protocol method, per the
     ADR-032 §"Contract tests" table:

     | Protocol method                                | Failure shape       |
     |------------------------------------------------|---------------------|
     | OAuth2Flow.discover_client_credentials         | raises              |
     | OAuth2Flow.authorize                           | returns_partial     |
     | CallbackListener.wait_for_callback             | times_out           |
     | TokenStore.store                               | unauthorized        |
     | RefreshableToken.refresh                       | unavailable         |
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from kairix.connect.listener import LocalhostCallbackListener
from kairix.connect.oauth2.google import GOOGLE_TOKEN_URI, GoogleOAuth2Flow
from kairix.connect.oauth2.slack import SLACK_TOKEN_URI, SlackOAuth2Flow
from kairix.connect.protocols import (
    BrowserLauncher,
    CallbackListener,
    CallbackResult,
    CallbackTimeoutError,
    CapturedTokens,
    ClientCredentials,
    OAuth2Flow,
    RefreshableToken,
    RefreshUnavailableError,
    TokenStore,
    TokenStoreUnauthorizedError,
)
from kairix.connect.refresh import GoogleRefreshableToken, GoogleRefreshState, StaticRefreshableToken
from kairix.connect.store.azure_kv_store import AzureKeyVaultTokenStore
from kairix.connect.store.file_store import FileTokenStore
from kairix.connect.store.stdout_store import StdoutTokenStore
from tests.fakes import (
    FakeBrowserLauncher,
    FakeCallbackListener,
    FakeRefreshableToken,
    FakeTokenStore,
)

pytestmark = pytest.mark.contract


# ---------------------------------------------------------------------------
# Conformance checks — every real impl + fake satisfies its Protocol
# ---------------------------------------------------------------------------


def test_oauth2_flow_real_implementation_satisfies_protocol(tmp_path: Path) -> None:
    """:class:`GoogleOAuth2Flow` satisfies :class:`OAuth2Flow`."""
    cs = tmp_path / "cs.json"
    cs.write_text('{"installed":{"client_id":"x","client_secret":"y"}}')
    flow = GoogleOAuth2Flow(service_area="gmail", client_secret_path=cs)
    assert isinstance(flow, OAuth2Flow)


def test_slack_oauth2_flow_satisfies_protocol() -> None:
    """:class:`SlackOAuth2Flow` satisfies :class:`OAuth2Flow` (F43)."""
    flow = SlackOAuth2Flow(workspace="alpha", client_id="x", client_secret="y")
    assert isinstance(flow, OAuth2Flow)


def test_callback_listener_real_satisfies_protocol() -> None:
    """:class:`LocalhostCallbackListener` satisfies :class:`CallbackListener`."""
    import socket as sk

    with sk.socket(sk.AF_INET, sk.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    listener = LocalhostCallbackListener(port=port)
    try:
        assert isinstance(listener, CallbackListener)
    finally:
        listener.close()


def test_callback_listener_fake_satisfies_protocol() -> None:
    fake = FakeCallbackListener()
    assert isinstance(fake, CallbackListener)


def test_token_store_real_implementations_satisfy_protocol() -> None:
    assert isinstance(FileTokenStore(), TokenStore)
    assert isinstance(StdoutTokenStore(stream=io.StringIO()), TokenStore)
    assert isinstance(AzureKeyVaultTokenStore(vault_name="x"), TokenStore)


def test_token_store_fake_satisfies_protocol() -> None:
    assert isinstance(FakeTokenStore(), TokenStore)


def test_refreshable_token_real_implementations_satisfy_protocol() -> None:
    state = GoogleRefreshState(client_id="x", client_secret="y", refresh_token="z", token_uri="https://x/")
    assert isinstance(GoogleRefreshableToken(state=state), RefreshableToken)
    assert isinstance(StaticRefreshableToken(token="x"), RefreshableToken)


def test_refreshable_token_fake_satisfies_protocol() -> None:
    assert isinstance(FakeRefreshableToken(), RefreshableToken)


def test_browser_launcher_fake_satisfies_protocol() -> None:
    assert isinstance(FakeBrowserLauncher(), BrowserLauncher)


# ---------------------------------------------------------------------------
# F68 failure-injection contract tests — one per Protocol method
# ---------------------------------------------------------------------------


def test_oauth2_flow_discover_raises_when_client_secret_missing(tmp_path: Path) -> None:
    """F68 ``raises`` shape: missing client_secret.json → :class:`FileNotFoundError`."""
    flow = GoogleOAuth2Flow(
        service_area="gmail",
        client_secret_path=tmp_path / "missing.json",
    )
    with pytest.raises(FileNotFoundError, match=r"client_secret\.json not found"):
        flow.discover_client_credentials()


def test_oauth2_flow_authorize_returns_partial_when_no_refresh_token(tmp_path: Path) -> None:
    """F68 ``returns_partial`` shape: token exchanger returning empty refresh_token.

    The contract requires a refresh_token; the default exchanger raises
    when Google omits it. A custom exchanger that returns an empty
    refresh_token would round-trip into the store layer — the contract
    is that the captured tokens carry the granted state, not that they
    inject validation. This test pins the round-trip semantics: an
    exchanger returning a CapturedTokens with empty refresh_token is
    surfaced to the caller verbatim (caller decides what's "partial").
    """
    cs = tmp_path / "cs.json"
    cs.write_text('{"installed":{"client_id":"x","client_secret":"y"}}')

    def partial_exchanger(_c: ClientCredentials, _code: str, _ru: str) -> CapturedTokens:
        return CapturedTokens(
            refresh_token="",  # PARTIAL — no long-lived credential granted
            access_token="short-lived-only",
            token_uri=GOOGLE_TOKEN_URI,
        )

    flow = GoogleOAuth2Flow(
        service_area="gmail",
        client_secret_path=cs,
        browser=FakeBrowserLauncher(),
        token_exchanger=partial_exchanger,
    )
    listener = FakeCallbackListener()
    tokens = flow.authorize(listener=listener)
    assert tokens.refresh_token == ""  # partial state surfaced verbatim
    assert tokens.access_token == "short-lived-only"


def test_callback_listener_wait_times_out_when_no_callback() -> None:
    """F68 ``times_out`` shape: listener with no callback → :class:`CallbackTimeoutError`."""
    fake = FakeCallbackListener(timeout=True)
    with pytest.raises(CallbackTimeoutError, match="simulated timeout"):
        fake.wait_for_callback(timeout_s=0.1)


def test_token_store_unauthorized_when_backend_rejects() -> None:
    """F68 ``unauthorized`` shape: backend rejects → :class:`TokenStoreUnauthorizedError`."""
    store = FakeTokenStore(raises=TokenStoreUnauthorizedError("simulated forbidden"))
    with pytest.raises(TokenStoreUnauthorizedError, match="simulated forbidden"):
        store.store(
            scope="connector",
            area="gmail",
            instance=None,
            tokens=CapturedTokens(refresh_token="r", access_token="a", token_uri="https://x/"),
            client=ClientCredentials(client_id="c", client_secret="s"),
        )


def test_refreshable_token_unavailable_when_refresh_fails() -> None:
    """F68 ``unavailable`` shape: refresh fails → :class:`RefreshUnavailableError`."""

    def refresh_fn(_state: GoogleRefreshState, _existing: str | None) -> tuple[str, float]:
        raise ConnectionError("network unreachable")

    state = GoogleRefreshState(client_id="x", client_secret="y", refresh_token="z", token_uri="https://x/")
    token = GoogleRefreshableToken(state=state, refresh_fn=refresh_fn)
    with pytest.raises(RefreshUnavailableError, match="refresh failed"):
        token.refresh()


def test_slack_oauth2_flow_discover_raises_when_credentials_empty() -> None:
    """F68 ``raises`` shape for Slack: missing client credentials surface at construction.

    Slack carries its credentials in the constructor (no on-disk
    ``client_secret.json`` like Google), so the ``raises`` shape
    triggers at ``SlackOAuth2Flow.__init__`` rather than at
    ``discover_client_credentials``. The contract is the same: an
    operator-correctable failure with F21 markers.
    """
    with pytest.raises(ValueError, match=r"client_id and client_secret are required"):
        SlackOAuth2Flow(workspace="alpha", client_id="", client_secret="csec")  # pragma: allowlist secret


def test_slack_oauth2_flow_authorize_returns_partial_no_refresh_token() -> None:
    """F68 ``returns_partial`` shape: Slack's documented no-refresh-token response.

    The contract is round-trip: the Flow returns whatever the
    exchanger gave it. Slack ALWAYS returns ``refresh_token=""`` for
    bot tokens (bot tokens never expire — see ADR-032 §"Refresh
    handling"). This test pins that the partial state is surfaced to
    the caller verbatim — the caller (CLI / store) doesn't reject it.
    """

    def slack_exchanger(_c: ClientCredentials, _code: str, _ru: str) -> CapturedTokens:
        return CapturedTokens(
            refresh_token="",  # PARTIAL — Slack never grants refresh tokens.
            access_token="",
            token_uri=SLACK_TOKEN_URI,
            bot_token="xoxb-partial",
        )

    flow = SlackOAuth2Flow(
        workspace="alpha",
        client_id="cid",
        client_secret="csec",  # pragma: allowlist secret
        browser=FakeBrowserLauncher(),
        token_exchanger=slack_exchanger,
    )
    tokens = flow.authorize(listener=FakeCallbackListener())
    # Caller sees the partial state verbatim — refresh_token is "".
    assert tokens.refresh_token == ""
    # The bot_token field carries the Slack credential the connector reads.
    assert tokens.bot_token == "xoxb-partial"


# ---------------------------------------------------------------------------
# Round-trip / shape pinning
# ---------------------------------------------------------------------------


def test_captured_tokens_frozen() -> None:
    """:class:`CapturedTokens` is frozen — F42 contract."""
    tokens = CapturedTokens(refresh_token="r", access_token="a", token_uri="https://x/")
    with pytest.raises(AttributeError):
        tokens.refresh_token = "mutated"  # type: ignore[misc]  # F3 rationale: frozen dataclass — intentional assignment to prove the freeze


def test_write_report_frozen() -> None:
    from kairix.connect.protocols import WriteReport

    report = WriteReport(canonical_names=("a", "b"), backend="file", target="/tmp/x")
    with pytest.raises(AttributeError):
        report.backend = "mutated"  # type: ignore[misc]  # F3 rationale: frozen dataclass — intentional assignment to prove the freeze


def test_callback_result_frozen() -> None:
    result = CallbackResult(code="c", state=None)
    with pytest.raises(AttributeError):
        result.code = "mutated"  # type: ignore[misc]  # F3 rationale: frozen dataclass — intentional assignment to prove the freeze
