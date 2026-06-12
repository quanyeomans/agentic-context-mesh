"""GitHub App OAuth2 flow — JWT signing + installation-id capture.

Per ADR-032 §"github_app" + the Agent C dispatch brief: GitHub App
installations are NOT a standard OAuth2 user flow. The shape is:

  1. Operator creates a GitHub App at github.com/settings/apps, sets
     permissions (Contents:Read + Issues:Read for the kairix
     connector), generates a private key (PEM), and downloads it.
  2. Operator installs the App into their org/account via the GitHub
     UI; after install completes GitHub redirects to a configurable
     callback URL with ``?installation_id=12345&setup_action=install``.
  3. At runtime kairix signs a JWT with the App's private key (RS256,
     10-min validity) and exchanges it via
     ``POST /app/installations/{installation_id}/access_tokens`` for a
     short-lived installation access token (1h, cached in memory).

The JWT signing key (PEM file on disk) is the long-lived credential;
the installation access token is ephemeral.

The Protocol surface :class:`OAuth2Flow` was designed around the
standard code-exchange shape (Google). GitHub App doesn't fit cleanly:

  * ``discover_client_credentials`` returns the App id (as
    ``client_id``) and the PEM private key (as ``client_secret``) —
    these are repurposed slots, see the class docstring.
  * ``authorize`` opens the browser to the App's install URL and
    captures the ``installation_id`` from the redirect callback. The
    returned :class:`CapturedTokens` carries an empty ``refresh_token``
    (there is none — the JWT signing key plays that role) and the
    ``installation_id`` lives in ``CapturedTokens.metadata``.

The shape mismatch is documented inline; the alternative was a wider
Protocol surface or per-service Protocol families (rejected to keep
the abstraction useful — see ADR-032 follow-up #3).

The ``pyjwt`` library is imported lazily inside :func:`_default_token_exchanger`
so module import succeeds without ``pyjwt[crypto]`` installed — the
operator only needs the library when they actually run
``kairix connect github-app`` against a live GitHub endpoint.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from kairix.connect.protocols import (
    BrowserLauncher,
    CallbackListener,
    CapturedTokens,
    ClientCredentials,
)

# Canonical GitHub endpoints — pinned as module constants so the F17
# "no duplicated string ≥3x" rule stays clean and tests reference the
# canonical endpoint by name.
GITHUB_API_BASE = "https://api.github.com"
GITHUB_APP_INSTALL_URL_TEMPLATE = "https://github.com/apps/{app_slug}/installations/new"
GITHUB_JWT_AUDIENCE_TOKEN_URI = "https://api.github.com/app/installations/access_tokens"  # noqa: S105 — GitHub API URL pinned for CapturedTokens.token_uri, not a credential

# Service area string fed to ``canonical_secret_name`` for GitHub App
# credentials. Matches ADR-031 + the GitHub connector's
# ``_resolve_credentials_from_secrets`` call site (uses ``area="github"``).
GITHUB_APP_SERVICE_AREA = "github"

# Metadata key :meth:`GitHubAppFlow.authorize` stores the captured
# installation id under in ``CapturedTokens.metadata``. Already the
# canonical kebab-case leaf name, so the stores' metadata walk
# (``kairix.connect.store.leaves._meta_pair``) writes it verbatim and
# the GitHub connector's credential resolver reads the same name.
# The wizard's source-OAuth layer imports this constant rather than
# re-declaring the string.
GITHUB_INSTALLATION_ID_METADATA_KEY = "installation-id"

# Default scopes tuple for the GitHub App flow. GitHub Apps don't use
# OAuth scope strings — permissions are configured on the App itself,
# not granted at install time. The tuple is empty by design; the
# Protocol-mandated attribute exists so isinstance(...) against
# OAuth2Flow succeeds.
GITHUB_APP_DEFAULT_SCOPES: tuple[str, ...] = ()

# JWT signing parameters per GitHub's docs:
#   https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-json-web-token-jwt-for-a-github-app
# 10-min validity matches GitHub's documented maximum for App JWTs;
# the spec recommends 9 min to absorb clock skew between kairix and
# GitHub. We pick 540s (9 min) to match.
JWT_LIFETIME_S = 540
JWT_ALGORITHM = "RS256"

# Per-error-message prefix kept module-local (F17).
_ERR_PREFIX = "kairix connect: "


def _read_private_key(path: Path) -> str:
    """Load a PEM-encoded RSA private key from ``path``.

    Returns the raw PEM text. Raises :class:`FileNotFoundError` /
    :class:`ValueError` with F21-compliant messages when the file is
    missing or doesn't look like a PEM private key.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"{_ERR_PREFIX}GitHub App private key not found at {path}. "
            f"fix: download the private key from github.com/settings/apps -> "
            f"<your-app> -> 'Private keys' -> 'Generate a private key' "
            f"(downloads a .pem file). "
            f"next: pass --private-key-path <downloaded-path> to kairix connect github-app. "
            f"run: kairix connect github-app --app-id <id> --private-key-path {path}",
        )
    try:
        contents = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FileNotFoundError(
            f"{_ERR_PREFIX}cannot read GitHub App private key at {path}: {exc}. "
            f"fix: confirm the file is readable by the current user "
            f"(``chmod 600 {path}`` if you own it). "
            f"next: re-run kairix connect github-app after fixing permissions. "
            f"run: chmod 600 {path}",
        ) from exc
    if "BEGIN" not in contents or "PRIVATE KEY" not in contents:
        raise ValueError(
            f"{_ERR_PREFIX}{path} does not look like a PEM private key "
            f"(missing 'BEGIN' or 'PRIVATE KEY' marker). "
            f"fix: re-download the private key from github.com/settings/apps -> "
            f"<your-app> -> 'Private keys' -> 'Generate a private key'. "
            f"next: confirm you downloaded the .pem file, not the .pub or App manifest. "
            f"run: kairix connect github-app --app-id <id> --private-key-path <path>",
        )
    return contents


class GitHubAppFlow:
    """Run the GitHub App install flow + installation-id capture.

    Args:
      app_id: The numeric GitHub App id (visible at
        github.com/settings/apps/<your-app> in the "About" section).
      private_key_path: Path to the App's PEM private key.
      app_slug: The App's URL slug (e.g. ``"kairix-bot"``). Drives the
        install URL ``https://github.com/apps/<slug>/installations/new``.
        Defaults to ``"kairix-bot"``; operators with a different slug
        pass ``--app-slug`` on the CLI.
      browser: :class:`BrowserLauncher` — defaults to a wrapper around
        ``webbrowser.open``. Tests inject a recording fake.
      install_url_builder: Test seam — replaces the install-URL
        construction so tests don't need a live App.
      token_exchanger: Test seam — replaces the JWT-sign-and-exchange
        step so tests don't need the ``pyjwt`` library installed.
        Receives ``(app_id, private_key_pem, installation_id)`` and
        returns the installation access token as a string.

    The ``service_area`` attribute is ``"github"`` to match
    ADR-031 + the connector's resolver. The ``scopes`` tuple is empty
    by design — GitHub Apps configure permissions on the App itself,
    not via OAuth scope strings.
    """

    service_area: str = GITHUB_APP_SERVICE_AREA
    scopes: tuple[str, ...] = GITHUB_APP_DEFAULT_SCOPES

    def __init__(
        self,
        *,
        app_id: str,
        private_key_path: Path,
        app_slug: str = "kairix-bot",
        browser: BrowserLauncher | None = None,
        install_url_builder: Callable[[str], str] | None = None,
        token_exchanger: Callable[[str, str, str], str] | None = None,
    ) -> None:
        if not app_id:
            raise ValueError(
                f"{_ERR_PREFIX}--app-id is required for kairix connect github-app. "
                f"fix: pass --app-id <numeric-id> from github.com/settings/apps/<your-app>. "
                f"next: copy the 'App ID' number from the App's 'About' section. "
                f"run: kairix connect github-app --app-id <id> --private-key-path <path>",
            )
        self._app_id = app_id
        self._private_key_path = private_key_path
        self._app_slug = app_slug
        self._browser = browser if browser is not None else _DefaultBrowser()
        self._install_url_builder = install_url_builder
        self._token_exchanger = token_exchanger

    def discover_client_credentials(self) -> ClientCredentials:
        """Read the App id + PEM private key from the operator-supplied source.

        Returns a :class:`ClientCredentials` whose slots are repurposed:

          * ``client_id`` carries the App id (the numeric identifier
            visible in the GitHub App settings page).
          * ``client_secret`` carries the full PEM private key text.

        The slot repurposing is documented in this class's module
        docstring; the alternative (per-service Protocol families) was
        rejected per ADR-032 follow-up #3 to keep the abstraction
        useful across services.
        """
        pem = _read_private_key(self._private_key_path)
        return ClientCredentials(client_id=self._app_id, client_secret=pem)

    def authorize(self, *, listener: CallbackListener) -> CapturedTokens:
        """Open the App install URL, capture the installation_id from callback.

        Steps:
          1. Validate + read the private key (raises early so the
             operator sees the actionable error before the browser opens).
          2. Build the install URL (configurable via ``app_slug``).
          3. Open the operator's browser to the install URL.
          4. Block on ``listener.wait_for_callback`` for the GitHub
             install redirect; capture ``installation_id`` from
             ``CallbackResult.params``.
          5. Sign a JWT with the App's private key and exchange it for
             an initial installation access token (cached for the
             connector's first API call).
          6. Return a :class:`CapturedTokens` carrying the installation
             access token (in ``access_token``) and the
             ``installation_id`` (in ``metadata``). ``refresh_token`` is
             empty by design — the JWT signing key is the long-lived
             credential, not a separate refresh token.
        """
        client = self.discover_client_credentials()
        install_url = self._build_install_url()
        self._browser.open(install_url)
        callback = listener.wait_for_callback()
        installation_id = callback.params.get("installation_id") or callback.code
        if not installation_id:
            raise ValueError(
                f"{_ERR_PREFIX}GitHub install callback did not carry an installation_id. "
                f"fix: re-run kairix connect github-app and complete the install via the "
                f"opened browser tab. "
                f"next: confirm the App's 'Setup URL' (in github.com/settings/apps/<your-app>) "
                f"matches the listener's redirect URI ({listener.redirect_uri}). "
                f"run: kairix connect github-app --app-id <id> --private-key-path <path>",
            )
        access_token = self._exchange_for_installation_token(
            client.client_id,
            client.client_secret,
            installation_id,
        )
        return CapturedTokens(
            refresh_token="",  # GitHub App has no refresh token — JWT key is the long-lived credential
            access_token=access_token,
            token_uri=GITHUB_JWT_AUDIENCE_TOKEN_URI,
            metadata={GITHUB_INSTALLATION_ID_METADATA_KEY: installation_id},
        )

    def _build_install_url(self) -> str:
        if self._install_url_builder is not None:
            return self._install_url_builder(self._app_slug)
        return GITHUB_APP_INSTALL_URL_TEMPLATE.format(app_slug=self._app_slug)

    def _exchange_for_installation_token(
        self,
        app_id: str,
        private_key_pem: str,
        installation_id: str,
    ) -> str:
        if self._token_exchanger is not None:
            return self._token_exchanger(app_id, private_key_pem, installation_id)
        return _default_token_exchanger(app_id, private_key_pem, installation_id)


def _default_token_exchanger(
    app_id: str,
    private_key_pem: str,
    installation_id: str,
) -> str:
    """Sign an App JWT + POST to GitHub for an installation access token.

    Lazy-imports ``pyjwt`` and ``httpx`` so unit tests that inject a
    ``token_exchanger`` never hit this path. Production callers without
    injection rely on both being installed.
    """
    try:
        import jwt  # PyJWT
    except ImportError as exc:
        raise RuntimeError(
            _ERR_PREFIX + "pyjwt is not installed. "
            "fix: pip install 'pyjwt[crypto]>=2.10'. "
            "next: re-run kairix connect github-app. "
            "run: pip install 'pyjwt[crypto]>=2.10'",
        ) from exc
    try:
        import httpx
    except ImportError as exc:
        raise RuntimeError(
            _ERR_PREFIX + "httpx is not installed. "
            "fix: pip install 'httpx>=0.27' (already a kairix dep — should be installed). "
            "next: confirm your kairix install is intact: pip show httpx. "
            "run: pip install 'httpx>=0.27'",
        ) from exc
    now = int(time.time())
    payload = {
        # 60s back-dated iat per GitHub's recommendation to absorb
        # clock skew between kairix and GitHub.
        "iat": now - 60,
        "exp": now + JWT_LIFETIME_S,
        "iss": app_id,
    }
    try:
        signed_jwt = jwt.encode(payload, private_key_pem, algorithm=JWT_ALGORITHM)
    except Exception as exc:
        raise RuntimeError(
            _ERR_PREFIX + f"GitHub App JWT signing failed: {exc}. "
            "fix: confirm the private key is a valid RSA PEM "
            "(re-download from github.com/settings/apps -> <your-app> -> 'Private keys'). "
            "next: re-run kairix connect github-app after replacing the key file. "
            "run: kairix connect github-app --app-id <id> --private-key-path <path>",
        ) from exc
    url = f"{GITHUB_API_BASE}/app/installations/{installation_id}/access_tokens"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {signed_jwt}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    response = httpx.post(url, headers=headers, timeout=30.0)
    if response.status_code >= 400:
        # F15-clean: the response body may contain an OAuth error envelope;
        # we surface the status + a one-line GitHub error code, not the
        # raw body, to avoid leaking transient tokens.
        try:
            error_code = response.json().get("message", "unknown")
        except (ValueError, TypeError):
            error_code = "unknown"
        raise RuntimeError(
            _ERR_PREFIX + f"GitHub rejected installation-token exchange "
            f"({response.status_code}): {error_code}. "
            "fix: confirm the App id matches the JWT issuer and the installation_id is correct "
            "(re-run kairix connect github-app to capture a fresh installation_id). "
            "next: confirm the App has Contents:Read + Issues:Read permissions configured "
            "(github.com/settings/apps/<your-app> -> Permissions & events). "
            "run: kairix connect github-app --app-id <id> --private-key-path <path>",
        )
    body = response.json()
    token = body.get("token")
    if not isinstance(token, str) or not token:
        raise RuntimeError(
            _ERR_PREFIX + "GitHub installation-token response missing 'token' field. "
            "fix: this is a GitHub API change — file an issue with the response details. "
            "next: re-run kairix connect github-app to retry. "
            "run: kairix connect github-app --app-id <id> --private-key-path <path>",
        )
    return token


class _DefaultBrowser:
    """Default :class:`BrowserLauncher` — calls :func:`webbrowser.open`.

    Mirrors the helper in :mod:`kairix.connect.oauth2.google` +
    :mod:`kairix.connect.oauth2.slack` (kept per-module rather than
    centralised so the lazy import surface stays narrow and the
    default browser doesn't get exercised by tests that inject a
    ``BrowserLauncher``).
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
                "kairix.connect.github_app: _DefaultBrowser.open suppressed by KAIRIX_CONNECT_DISABLE_BROWSER=1; "
                "url=%s. fix: this should only fire in pytest — production must leave the env var unset. "
                "next: confirm the calling test injects browser=FakeBrowserLauncher() on its GitHubAppFlow. "
                "run: KAIRIX_CONNECT_DISABLE_BROWSER= kairix connect github-app ...",
                url,
            )
            return False
        import webbrowser  # pragma: no cover — live browser path

        return webbrowser.open(url)  # pragma: no cover — live browser path


# Protocol conformance smoke check on the default browser (proves the
# Protocol attaches at module import time without GitHubAppFlow's
# required constructor args).
_BROWSER_CHECK: BrowserLauncher = _DefaultBrowser()


__all__ = [
    "GITHUB_API_BASE",
    "GITHUB_APP_DEFAULT_SCOPES",
    "GITHUB_APP_INSTALL_URL_TEMPLATE",
    "GITHUB_APP_SERVICE_AREA",
    "GITHUB_INSTALLATION_ID_METADATA_KEY",
    "GITHUB_JWT_AUDIENCE_TOKEN_URI",
    "JWT_ALGORITHM",
    "JWT_LIFETIME_S",
    "GitHubAppFlow",
]
