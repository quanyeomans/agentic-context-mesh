"""F68 failure-injection contract tests for :class:`OAuth2Flow`.

Per ADR-032 §"Contract tests" each Protocol method gets a
failure-injection test naming the F68 shape in the function name:

  * ``discover_client_credentials`` → ``raises``
  * ``authorize`` → ``returns_partial``
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kairix.connect.oauth2.google import GOOGLE_TOKEN_URI, GoogleOAuth2Flow
from kairix.connect.protocols import CapturedTokens, ClientCredentials
from tests.fakes import FakeBrowserLauncher, FakeCallbackListener

pytestmark = pytest.mark.contract


def test_discover_client_credentials_raises_file_not_found_when_path_missing(tmp_path: Path) -> None:
    """``discover_client_credentials`` raises when the operator-supplied path doesn't exist."""
    flow = GoogleOAuth2Flow(
        service_area="gmail",
        client_secret_path=tmp_path / "absent.json",
    )
    with pytest.raises(FileNotFoundError, match=r"client_secret\.json not found"):
        flow.discover_client_credentials()


def test_authorize_returns_partial_when_no_refresh_token_granted(tmp_path: Path) -> None:
    """``authorize`` surfaces a partial :class:`CapturedTokens` (empty refresh_token).

    The contract is round-trip: the OAuth2Flow returns whatever the
    exchanger gave it. Caller-side validation (e.g. the connector
    refusing to store an empty refresh_token) lives at the caller, not
    inside the flow.
    """
    cs = tmp_path / "cs.json"
    cs.write_text('{"installed":{"client_id":"x","client_secret":"y"}}')

    def partial_exchanger(_c: ClientCredentials, _code: str, _ru: str) -> CapturedTokens:
        return CapturedTokens(
            refresh_token="",  # partial — no long-lived credential
            access_token="short-lived-only",
            token_uri=GOOGLE_TOKEN_URI,
        )

    flow = GoogleOAuth2Flow(
        service_area="gmail",
        client_secret_path=cs,
        browser=FakeBrowserLauncher(),
        token_exchanger=partial_exchanger,
    )
    tokens = flow.authorize(listener=FakeCallbackListener())
    assert tokens.refresh_token == ""
    assert tokens.access_token == "short-lived-only"
