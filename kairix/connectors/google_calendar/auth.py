"""Auth resolution for the Google Calendar connector — auto-refresh wrapper.

Mirror of :mod:`kairix.connectors.google_drive.auth` for Calendar. Per
ADR-032 §"Refresh handling (connector-side)" the legacy path read a
single ``access_token`` and surfaced ``CredentialExpiredError`` on 401
with no automatic refresh; this module fixes that by building a
:class:`GoogleRefreshableToken` from the canonical credential set.

Layering: lives under ``kairix.connectors.google_calendar.*`` per F35;
imports ``kairix.connect.refresh`` per ADR-032's allowed direction.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from kairix.connect.refresh import GoogleRefreshableToken, GoogleRefreshState


@dataclass(frozen=True)
class GoogleCalendarAuthBlob:
    """Full canonical credential set the Calendar connector can refresh from."""

    client_id: str
    client_secret: str
    refresh_token: str
    initial_access_token: str | None = None


def build_refreshable_token(blob: GoogleCalendarAuthBlob) -> GoogleRefreshableToken:
    """Build a :class:`GoogleRefreshableToken` for the Calendar credential set.

    Same 1-hour initial-expiry assumption as the Drive sibling — see
    :func:`kairix.connectors.google_drive.auth.build_refreshable_token`.
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

    F6-clean injectable seam mirroring the Drive sibling.
    """
    from kairix.secrets import get_secret

    return get_secret(name, required=False)


def resolve_calendar_access_token(
    *,
    get_secret_fn: Callable[[str], str | None] = _default_get_secret,
) -> str:
    """Resolve the Calendar OAuth credentials + mint a fresh access token.

    Returns the bearer string (not a dataclass) because the existing
    Calendar connector's :class:`GoogleCalendarConfig` carries only a
    ``access_token: str`` field. The connector's
    :func:`make_connector` calls this when no explicit
    ``access_token`` is supplied in config.

    Reads:
      * ``connector-google-calendar-client-id``
      * ``connector-google-calendar-client-secret``
      * ``connector-google-calendar-refresh-token``
      * ``connector-google-calendar-access-token``

    Per ADR-031 canonical naming. Falls back to the static
    ``connector-google-calendar-access-token`` if the full set is not
    available.

    Args:
      get_secret_fn: Test seam — injectable callable resolving each
        canonical secret name. Production default delegates to
        :func:`kairix.secrets.get_secret`.
    """
    client_id = get_secret_fn("connector-google-calendar-client-id") or ""
    client_secret = get_secret_fn("connector-google-calendar-client-secret") or ""
    refresh_token = get_secret_fn("connector-google-calendar-refresh-token") or ""
    access_token = get_secret_fn("connector-google-calendar-access-token") or ""

    if not (client_id and client_secret and refresh_token):
        if access_token:
            return access_token
        raise OSError(
            "google_calendar: no usable OAuth credentials in the secret backend. "
            "fix: run kairix connect google-calendar --client-secret-path <path> to capture the full "
            "client-id + client-secret + refresh-token + access-token set, "
            "OR set connector-google-calendar-access-token directly. "
            "next: see kairix/connect/README.md for the OAuth capture walkthrough. "
            "run: kairix connect google-calendar --client-secret-path ~/Downloads/client_secret.json",
        )

    blob = GoogleCalendarAuthBlob(
        client_id=client_id,
        client_secret=client_secret,
        refresh_token=refresh_token,
        initial_access_token=access_token or None,
    )
    token = build_refreshable_token(blob)
    if not access_token:
        token.refresh()
    headers = token.headers()
    auth = headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[len("Bearer ") :]
    return auth


__all__ = [
    "GoogleCalendarAuthBlob",
    "build_refreshable_token",
    "resolve_calendar_access_token",
]
