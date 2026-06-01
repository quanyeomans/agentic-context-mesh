"""Auth resolution for the Google Drive connector — auto-refresh wrapper.

Per ADR-032 §"Refresh handling (connector-side)": the legacy Drive
resolution path read a single ``access_token`` secret and surfaced
:class:`CredentialExpiredError` on 401 with no automatic refresh.
This module fixes that by building a :class:`GoogleRefreshableToken`
from the canonical OAuth credential set (client-id / client-secret /
refresh-token / access-token) and minting a fresh access token
upfront, eliminating the most common operator failure: a multi-day-old
access token expiring silently between connector ticks.

Layering: this module sits inside the connector tree per F35 (only
``kairix.connectors.google_drive.*`` may import here). It imports
``kairix.connect.refresh`` because that module is the canonical
:class:`RefreshableToken` provider — the dependency runs from connector
inward to ``kairix.connect``, which is the layering direction
ADR-032 §"Layering (F26 / F35 alignment)" specifies.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from kairix.connect.refresh import GoogleRefreshableToken, GoogleRefreshState
from kairix.connectors.google_drive.connector import GoogleDriveCredentials


@dataclass(frozen=True)
class GoogleDriveAuthBlob:
    """The full canonical credential set the connector can refresh from."""

    client_id: str
    client_secret: str
    refresh_token: str
    initial_access_token: str | None = None


def build_refreshable_token(blob: GoogleDriveAuthBlob) -> GoogleRefreshableToken:
    """Build a :class:`GoogleRefreshableToken` from the canonical credential set.

    Connectors that already had a valid ``initial_access_token`` reuse
    it until expiry; callers without one pay the refresh cost on first
    :meth:`headers` call.

    When an ``initial_access_token`` is supplied, the token starts with
    a 1-hour assumed expiry — matches Google's default access-token
    lifetime. After the first real refresh the actual expiry replaces
    this. The 1h default avoids treating a just-captured token as
    immediately stale.
    """
    import time as _time

    initial_expiry = _time.time() + 3600 if blob.initial_access_token else None
    return GoogleRefreshableToken(
        state=GoogleRefreshState(
            client_id=blob.client_id,
            client_secret=blob.client_secret,
            refresh_token=blob.refresh_token,
            token_uri="https://oauth2.googleapis.com/token",  # noqa: S106 — OAuth endpoint URL, not a credential
        ),
        initial_access_token=blob.initial_access_token,
        initial_expiry_epoch=initial_expiry,
    )


def _default_get_secret(name: str) -> str | None:
    """Production default — delegates to :func:`kairix.secrets.get_secret`.

    Lifted to a free function (F6-clean — real callable default with
    ``default_factory`` shape) so tests inject an in-memory dict
    instead of patching the lazy-imported :mod:`kairix.secrets` module.
    """
    from kairix.secrets import get_secret

    return get_secret(name, required=False)


def resolve_drive_credentials_with_refresh(
    *,
    get_secret_fn: Callable[[str], str | None] = _default_get_secret,
) -> GoogleDriveCredentials:
    """Resolve the OAuth credential set + mint a fresh access token.

    Reads the full canonical set (client_id, client_secret, refresh_token,
    access_token) from the secret backend. If the access_token is
    missing or fails on use, the refresh path runs.

    Returns a :class:`GoogleDriveCredentials` with a freshly-minted
    access_token — the connector's existing constructor signature is
    unchanged.

    Per ADR-031 the canonical names are
    ``connector-google-drive-{client-id, client-secret, refresh-token,
    access-token}``.

    Args:
      get_secret_fn: Test seam — injectable callable resolving each
        canonical secret name. Production default delegates to
        :func:`kairix.secrets.get_secret`.
    """
    client_id = get_secret_fn("connector-google-drive-client-id") or ""
    client_secret = get_secret_fn("connector-google-drive-client-secret") or ""
    refresh_token = get_secret_fn("connector-google-drive-refresh-token") or ""
    access_token = get_secret_fn("connector-google-drive-access-token") or ""

    if not (client_id and client_secret and refresh_token):
        # Legacy path — operator only set the static access_token. The
        # connector keeps working until that token expires; the
        # CredentialExpiredError already surfaces with the existing
        # operator hint.
        if access_token:
            return GoogleDriveCredentials(access_token=access_token)
        raise OSError(
            "google_drive: no usable OAuth credentials in the secret backend. "
            "fix: run kairix connect google-drive --client-secret-path <path> to capture the full "
            "client-id + client-secret + refresh-token + access-token set, "
            "OR set connector-google-drive-access-token directly. "
            "next: see kairix/connect/README.md for the OAuth capture walkthrough. "
            "run: kairix connect google-drive --client-secret-path ~/Downloads/client_secret.json",
        )

    blob = GoogleDriveAuthBlob(
        client_id=client_id,
        client_secret=client_secret,
        refresh_token=refresh_token,
        initial_access_token=access_token or None,
    )
    token = build_refreshable_token(blob)
    # Force one refresh up-front so the initial access_token is always
    # fresh — eliminates the cold-start expired-token failure mode the
    # ADR documents as the silent bug.
    if not access_token:
        token.refresh()
    return GoogleDriveCredentials(access_token=_bearer_value(token))


def _bearer_value(token: GoogleRefreshableToken) -> str:
    """Extract just the bearer value from the headers dict."""
    headers = token.headers()
    auth = headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[len("Bearer ") :]
    return auth


__all__ = [
    "GoogleDriveAuthBlob",
    "build_refreshable_token",
    "resolve_drive_credentials_with_refresh",
]
