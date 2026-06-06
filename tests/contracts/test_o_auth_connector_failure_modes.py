"""F68 (ADR-024 Bundle A) — failure-mode contract for :class:`OAuthConnector`.

Two classmethods on the three-legged-OAuth flow surface:

  * ``oauth_authorization_url(state)`` — operator-visit URL builder.
  * ``oauth_code_to_token(code)`` — code→token-envelope builder.

The Protocol pins the SHAPE of the flow (classmethods, NOT instance
methods) because the flow happens BEFORE the connector instance exists.
Failure surface:

  * ``raises`` — surfaces typed exception when the input is malformed
    (empty state / empty code) for the inline failing impl; the
    real shipped connectors deliberately tolerate the empty case
    (they return a deterministic URL or envelope) so the orchestrator
    can probe the surface.
  * ``returns_partial`` — the shipped GitHub connector's
    ``oauth_code_to_token`` returns an envelope WITHOUT the
    access_token (the actual exchange happens in the callback handler);
    the partial-envelope shape is the documented contract.
"""

from __future__ import annotations

from typing import Any

import pytest

from kairix.core.protocols import OAuthConnector

pytestmark = pytest.mark.contract


class _FailingOAuthConnector:
    """Inline :class:`OAuthConnector` with raises-knobs."""

    _raise_on_url: BaseException | None = None
    _raise_on_token: BaseException | None = None

    @classmethod
    def oauth_authorization_url(cls, state: str) -> str:
        del state
        if cls._raise_on_url is not None:
            raise cls._raise_on_url
        return "https://example.invalid/authorize"

    @classmethod
    def oauth_code_to_token(cls, code: str) -> dict[str, Any]:
        del code
        if cls._raise_on_token is not None:
            raise cls._raise_on_token
        return {"access_token": "x"}


def test_oauth_authorization_url_raises_propagates_typed_exception() -> None:
    """A URL-builder failure (e.g. invalid state encoding) surfaces —
    callers must NOT redirect the operator to a malformed URL.

    Sabotage proof: drop the ``raise`` in
    ``_FailingOAuthConnector.oauth_authorization_url``. Re-run:
    pytest.raises sees nothing. Restored.
    """

    class _RaisingURL(_FailingOAuthConnector):
        _raise_on_url = ValueError("F68-oauth-url-raises")

    conn: type[OAuthConnector] = _RaisingURL
    with pytest.raises(ValueError, match="F68-oauth-url-raises"):
        conn.oauth_authorization_url("state-value")


def test_oauth_code_to_token_raises_propagates_typed_exception() -> None:
    """A code-exchange failure surfaces — callers must NOT silently
    return an empty token dict because the next request would 401 with
    no diagnostic context.

    Sabotage proof: drop the ``raise`` in
    ``_FailingOAuthConnector.oauth_code_to_token``. Re-run:
    pytest.raises sees nothing. Restored.
    """

    class _RaisingToken(_FailingOAuthConnector):
        _raise_on_token = RuntimeError("F68-oauth-token-raises")

    conn: type[OAuthConnector] = _RaisingToken
    with pytest.raises(RuntimeError, match="F68-oauth-token-raises"):
        conn.oauth_code_to_token("code-value")
