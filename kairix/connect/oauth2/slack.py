"""Slack OAuth2 v2 flow — workspace-scoped bot-token capture.

Per ADR-032 §"Library choices": Slack distributes bot tokens via the
three-legged ``oauth.v2.access`` endpoint. Tokens are workspace-scoped
and long-lived (no refresh cycle); the canonical schema's ``instance``
slot disambiguates per-workspace tokens — ``kairix-connector-slack-
<workspace>-bot-token``.

The flow:

  1. Operator creates a Slack app at https://api.slack.com/apps and
     captures ``client_id`` + ``client_secret`` from the "Basic
     Information" page.
  2. Operator runs ``kairix connect slack --workspace alpha
     --client-id <id> --client-secret <secret>``.
  3. kairix opens the browser to Slack's authorize URL with the
     workspace's redirect_uri (``http://127.0.0.1:8080/oauth2callback``).
  4. The operator picks the workspace + approves the requested scopes.
  5. Slack redirects back with the authorization code; the localhost
     listener catches it.
  6. The flow exchanges the code for tokens via
     ``https://slack.com/api/oauth.v2.access``.
  7. The token store writes ``client-id``, ``client-secret``,
     ``bot-token``, and (when granted) ``app-token`` under the
     per-workspace canonical names.

Slack returns ``team_id`` + ``team_name`` in the OAuth response — used
in the operator-facing success summary but NOT written as canonical
secrets (those are organisational metadata, not auth material).

The Slack-shape :class:`kairix.connect.protocols.CapturedTokens`
carries ``bot_token`` populated and ``refresh_token=""`` (Slack bot
tokens never expire — the empty refresh_token is the documented F68
``returns_partial`` shape per ADR-032's contract-tests table).

Per the project standard, the code-exchange call uses ``httpx``
(already a top-level dep — see ``pyproject.toml``) rather than the
``slack_sdk`` package which is not yet adopted in this repo. The
exchange call is one POST request; pulling in ``slack_sdk`` for one
call is unjustified per the OSS-evaluation matrix in ADR-016.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from kairix.connect.protocols import (
    BrowserLauncher,
    CallbackListener,
    CapturedTokens,
    ClientCredentials,
)

# Slack's OAuth v2 endpoints. Pinned here so the captured
# :class:`CapturedTokens.token_uri` doesn't depend on any client-library
# default (and so tests can assert the verbatim value).
SLACK_AUTHORIZE_URL = "https://slack.com/oauth/v2/authorize"
SLACK_TOKEN_URI = "https://slack.com/api/oauth.v2.access"  # noqa: S105 — OAuth endpoint URL, not a credential

# Default bot scopes for the Slack connector. Mirrors the scope set
# declared by the Slack connector's existing OAuth v2 surface
# (``kairix/connectors/slack/connector.py::oauth_authorization_url``)
# so a workspace approved via ``kairix connect slack`` carries the
# scopes the connector actually requires at runtime.
DEFAULT_SLACK_BOT_SCOPES: tuple[str, ...] = (
    "channels:history",
    "channels:read",
    "groups:history",
    "groups:read",
    "im:history",
    "im:read",
    "mpim:history",
    "mpim:read",
    "users:read",
)

# Canonical service-area string fed to ``canonical_secret_name``. Slack
# always uses ``"slack"`` as the area; the workspace lives in the
# ``instance`` slot per ADR-032 §"Open questions" #1 resolution.
SLACK_SERVICE_AREA = "slack"

# Common error-message prefix — kept as a module constant so F17
# (no string literal ≥10 chars duplicated ≥3 times) stays clean.
_ERR_PREFIX = "kairix connect: "

# Operator-facing run hint repeated across every F21 error in this
# module. Hoisted per F17.
_RUN_HINT = "run: kairix connect slack --workspace <name> --client-id <id> --client-secret <secret>"

# OAuth2 form-parameter key used across the URL-builder + token-exchange
# payloads. Hoisted per F17 (≥3 occurrences across the module).
_REDIRECT_URI_KEY = "redirect_uri"


class SlackOAuth2Flow:
    """Run the Slack OAuth v2 authorize-and-exchange flow for one workspace.

    Args:
      workspace: Operator-supplied workspace identifier. Lands in the
        canonical-name ``instance`` slot — tokens for workspace
        ``alpha`` write to ``kairix-connector-slack-alpha-bot-token``;
        tokens for workspace ``coach`` write to
        ``kairix-connector-slack-coach-bot-token``. The workspace
        name is operator-chosen (typically the team's slug); Slack's
        ``team_id`` is stored as part of the bot-token state but the
        ``instance`` slot is what makes per-workspace tokens
        co-resident in the same KV.
      client_id: Slack app's OAuth client_id from
        https://api.slack.com/apps -> Basic Information.
      client_secret: Slack app's OAuth client_secret from the same page.
      scopes: Override the default bot scopes (rare — operators
        running a custom scope set pass an explicit tuple). Defaults
        to :data:`DEFAULT_SLACK_BOT_SCOPES`.
      browser: :class:`BrowserLauncher` — defaults to a wrapper around
        ``webbrowser.open``. Tests inject a recording fake.
      authorize_url_builder: Test seam — replaces the default
        authorize-URL construction. Receives
        ``(client, redirect_uri, scopes)`` and returns the URL string.
      token_exchanger: Test seam — replaces the default code-for-token
        exchange. Receives ``(client, code, redirect_uri)`` and returns
        a :class:`CapturedTokens`. Tests use this to avoid an outbound
        HTTP call to Slack.

    Exposes :attr:`team_id` + :attr:`team_name` AFTER :meth:`authorize`
    completes so the CLI's success summary can name the team the
    tokens belong to. Both default to empty before the exchange runs.
    """

    def __init__(
        self,
        *,
        workspace: str,
        client_id: str,
        client_secret: str,
        scopes: tuple[str, ...] | None = None,
        browser: BrowserLauncher | None = None,
        authorize_url_builder: Callable[[ClientCredentials, str, tuple[str, ...]], str] | None = None,
        token_exchanger: Callable[[ClientCredentials, str, str], CapturedTokens] | None = None,
        http_post: Callable[[str, dict[str, str]], dict[str, Any]] | None = None,
    ) -> None:
        if not workspace:
            raise ValueError(
                f"{_ERR_PREFIX}slack workspace must be a non-empty string. "
                f"fix: pass --workspace <name> on the command line. "
                f"next: see kairix/connect/README.md for the per-workspace canonical-naming shape. "
                f"run: kairix connect slack --workspace alpha --client-id <id> --client-secret <secret>",
            )
        if not client_id or not client_secret:
            raise ValueError(
                f"{_ERR_PREFIX}slack client_id and client_secret are required. "
                f"fix: pass --client-id and --client-secret OR pre-populate "
                f"kairix-connector-slack-<workspace>-{{client-id,client-secret}} in KV. "
                f"next: capture both from https://api.slack.com/apps -> Basic Information. "
                f"{_RUN_HINT}",
            )
        self.service_area = SLACK_SERVICE_AREA
        self.workspace = workspace
        self.scopes: tuple[str, ...] = scopes if scopes is not None else DEFAULT_SLACK_BOT_SCOPES
        self._client_id = client_id
        self._client_secret = client_secret
        self._browser = browser if browser is not None else _DefaultBrowser()
        self._authorize_url_builder = authorize_url_builder
        self._token_exchanger = token_exchanger
        self._http_post = http_post
        # Populated after authorize() — operator-facing team metadata.
        self.team_id: str = ""
        self.team_name: str = ""

    def discover_client_credentials(self) -> ClientCredentials:
        """Return the operator-supplied client_id + client_secret.

        Unlike Google (which reads from ``client_secret.json``), Slack
        operators paste the credential pair on the CLI or pre-populate
        them in KV — there's no equivalent of GCP's downloadable
        ``client_secret.json``. The values were validated in
        ``__init__``; this method just packages them.
        """
        return ClientCredentials(client_id=self._client_id, client_secret=self._client_secret)

    def authorize(self, *, listener: CallbackListener, timeout_s: float = 120.0) -> CapturedTokens:
        """Run the consent dance + token exchange against ``listener``.

        Steps:
          1. Build the authorize URL with the listener's ``redirect_uri``.
          2. Open the operator's browser to the consent screen.
          3. Block on ``listener.wait_for_callback`` for the code, honouring
             the operator-supplied ``timeout_s`` (``kairix connect --timeout``).
          4. Exchange the code for tokens via Slack's
             ``oauth.v2.access`` endpoint.
          5. Return the typed :class:`CapturedTokens` — ``bot_token``
             populated, ``refresh_token=""`` (documented partial state).
        """
        client = self.discover_client_credentials()
        redirect_uri = listener.redirect_uri
        authorize_url = self._build_authorize_url(client, redirect_uri)
        self._browser.open(authorize_url)
        callback = listener.wait_for_callback(timeout_s=timeout_s)
        return self._exchange_code(client, callback.code, redirect_uri)

    def _build_authorize_url(self, client: ClientCredentials, redirect_uri: str) -> str:
        if self._authorize_url_builder is not None:
            return self._authorize_url_builder(client, redirect_uri, self.scopes)
        return _default_authorize_url(client, redirect_uri, self.scopes)

    def _exchange_code(
        self,
        client: ClientCredentials,
        code: str,
        redirect_uri: str,
    ) -> CapturedTokens:
        if self._token_exchanger is not None:
            return self._token_exchanger(client, code, redirect_uri)
        tokens, team_id, team_name = _default_exchange_code(
            client,
            code,
            redirect_uri,
            http_post=self._http_post,
        )
        self.team_id = team_id
        self.team_name = team_name
        return tokens


def _default_authorize_url(
    client: ClientCredentials,
    redirect_uri: str,
    scopes: tuple[str, ...],
) -> str:
    """Build Slack's OAuth v2 authorize URL via stdlib only.

    Mirrors the shape ``slack_sdk.signature_verifier`` would build but
    uses ``urllib.parse.urlencode`` so we have no extra dep — the one
    POST that needs an HTTP client uses ``httpx`` (already a top-level
    dep).
    """
    from urllib.parse import urlencode

    params = {
        "client_id": client.client_id,
        "scope": ",".join(scopes),
        _REDIRECT_URI_KEY: redirect_uri,
        "user_scope": "",
    }
    return f"{SLACK_AUTHORIZE_URL}?{urlencode(params)}"


def _default_exchange_code(
    client: ClientCredentials,
    code: str,
    redirect_uri: str,
    http_post: Callable[[str, dict[str, str]], dict[str, Any]] | None = None,
) -> tuple[CapturedTokens, str, str]:
    """POST to Slack's ``oauth.v2.access`` and parse the response.

    Returns ``(tokens, team_id, team_name)`` — the CapturedTokens
    carries the bot_token; team_id + team_name are operator-facing
    metadata the CLI surfaces in the success summary but never writes
    as canonical secrets.

    Per the Slack OAuth v2 spec
    (https://api.slack.com/methods/oauth.v2.access), the response
    payload is::

        {
          "ok": true,
          "access_token": "xoxb-...",       # the bot token
          "token_type": "bot",
          "scope": "channels:history,...",
          "bot_user_id": "U_BOT",
          "team": {"id": "T...", "name": "..."}
        }

    The app-token (``xapp-...``) is NOT returned by ``oauth.v2.access``;
    operators capture it manually from the app's "Basic Information"
    page if they want Socket Mode. ``kairix connect slack`` skips it
    in the default flow — operators who need it can rerun with
    ``--app-token <token>`` to add it to the stored credential set.

    Args:
      http_post: Test seam — replaces the live ``httpx.post`` call so
        unit tests cover the response-parsing branches without
        monkeypatching the httpx module. Receives ``(url, form_data)``
        and returns the parsed JSON dict. Production callers leave
        this ``None`` and the live httpx path runs.
    """
    payload = (
        http_post(
            SLACK_TOKEN_URI,
            {
                "client_id": client.client_id,
                "client_secret": client.client_secret,
                "code": code,
                _REDIRECT_URI_KEY: redirect_uri,
            },
        )
        if http_post is not None
        else _live_slack_post(client, code, redirect_uri)
    )
    if not payload.get("ok"):
        error = payload.get("error", "unknown")
        raise RuntimeError(
            _ERR_PREFIX + f"slack oauth.v2.access returned ok=false (error={error!r}). "
            "fix: confirm client_id + client_secret match the Slack app at "
            "https://api.slack.com/apps and the redirect_uri matches the app's "
            "configured 'Redirect URLs' list (OAuth & Permissions page). "
            "next: re-run kairix connect slack after correcting the app config. "
            f"{_RUN_HINT}",
        )
    bot_token = str(payload.get("access_token") or "")
    if not bot_token:
        raise RuntimeError(
            _ERR_PREFIX + "slack oauth.v2.access returned an empty access_token. "
            "fix: confirm the Slack app has bot-scope permissions configured "
            "(OAuth & Permissions -> Scopes -> Bot Token Scopes). "
            "next: re-install the app to the workspace after adding scopes. "
            f"{_RUN_HINT}",
        )
    team = payload.get("team")
    team_id = str(team.get("id", "")) if isinstance(team, dict) else ""
    team_name = str(team.get("name", "")) if isinstance(team, dict) else ""
    tokens = CapturedTokens(
        refresh_token="",  # Slack bot tokens never expire — documented partial state per F68.
        access_token="",  # bot_token field carries the workspace credential instead.
        token_uri=SLACK_TOKEN_URI,
        bot_token=bot_token,
    )
    return tokens, team_id, team_name


# Live httpx call to Slack's oauth.v2.access; tests use http_post= seam
# on `_default_exchange_code` to exercise every parsing branch without
# an outbound network request — see test_connect_oauth2_slack.py.
def _live_slack_post(  # pragma: no cover — live network path
    client: ClientCredentials,
    code: str,
    redirect_uri: str,
) -> dict[str, Any]:
    """Production ``httpx.post`` to Slack's oauth.v2.access endpoint.

    Lazy-imports ``httpx`` so the module loads cleanly in environments
    where httpx isn't on the path (test contexts that inject
    ``http_post`` never reach here).
    """
    import httpx

    response = httpx.post(
        SLACK_TOKEN_URI,
        data={
            "client_id": client.client_id,
            "client_secret": client.client_secret,
            "code": code,
            _REDIRECT_URI_KEY: redirect_uri,
        },
        timeout=30.0,
    )
    response.raise_for_status()
    parsed: dict[str, Any] = response.json()
    return parsed


class _DefaultBrowser:
    """Default :class:`BrowserLauncher` — calls :func:`webbrowser.open`.

    Mirrors :class:`kairix.connect.oauth2.google._DefaultBrowser`; kept
    private to this module so a Slack-specific browser launcher can
    diverge from Google's without coupling.
    """

    def open(self, url: str) -> bool:
        # KAIRIX_CONNECT_DISABLE_BROWSER kill-switch: tests/conftest.py
        # sets this so any test that escapes the FakeBrowserLauncher
        # injection seam is hard-blocked instead of firing a real popup
        # on the operator's machine (2026-06-01 incident).
        from kairix.paths import connect_browser_disabled

        if connect_browser_disabled():
            import logging

            logging.getLogger(__name__).warning(
                "kairix.connect.slack: _DefaultBrowser.open suppressed by KAIRIX_CONNECT_DISABLE_BROWSER=1; "
                "url=%s. fix: this should only fire in pytest — production must leave the env var unset. "
                "next: confirm the calling test injects browser=FakeBrowserLauncher() on its SlackOAuth2Flow. "
                "run: KAIRIX_CONNECT_DISABLE_BROWSER= kairix connect slack ...",
                url,
            )
            return False
        import webbrowser  # pragma: no cover — live browser path

        return webbrowser.open(url)  # pragma: no cover — live browser path


# Protocol conformance smoke check — confirms _DefaultBrowser satisfies
# the BrowserLauncher Protocol at module-load time. SlackOAuth2Flow's
# Protocol conformance is exercised in tests/contracts/test_connect_protocols.py.
_BROWSER_CHECK: BrowserLauncher = _DefaultBrowser()


__all__ = [
    "DEFAULT_SLACK_BOT_SCOPES",
    "SLACK_AUTHORIZE_URL",
    "SLACK_SERVICE_AREA",
    "SLACK_TOKEN_URI",
    "SlackOAuth2Flow",
]
