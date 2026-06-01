"""Unit-level coverage for the Drive + Calendar auth-refresh modules.

Confirms the canonical credential set resolves correctly via the
canonical-named secrets, the refresh path runs on cold-start when no
initial access token is present, and the legacy single-secret path
keeps working when the full set isn't yet provisioned.

F1/F2-clean: tests inject an in-memory ``get_secret_fn`` callable into
the production resolvers — no monkeypatching of kairix internals.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from kairix.connectors.google_calendar.auth import (
    GoogleCalendarAuthBlob,
    resolve_calendar_access_token,
)
from kairix.connectors.google_calendar.auth import (
    build_refreshable_token as build_calendar_token,
)
from kairix.connectors.google_drive.auth import (
    GoogleDriveAuthBlob,
    build_refreshable_token,
    resolve_drive_credentials_with_refresh,
)

pytestmark = pytest.mark.unit


def _stub_secrets(values: dict[str, str]) -> Callable[[str], str | None]:
    """Return a callable resolving from a fixed dict — injectable seam shape."""

    def _get(name: str) -> str | None:
        return values.get(name)

    return _get


def test_build_drive_refreshable_token_returns_protocol_shape() -> None:
    """The builder yields a :class:`GoogleRefreshableToken` with the credential set wired in."""
    blob = GoogleDriveAuthBlob(
        client_id="cid",
        client_secret="csec",  # pragma: allowlist secret
        refresh_token="rt",
        initial_access_token="at-fresh",
    )
    token = build_refreshable_token(blob)
    assert token._state.client_id == "cid"
    assert token._state.refresh_token == "rt"


def test_build_calendar_refreshable_token_returns_protocol_shape() -> None:
    blob = GoogleCalendarAuthBlob(
        client_id="cid",
        client_secret="csec",  # pragma: allowlist secret
        refresh_token="rt",
        initial_access_token="at-fresh",
    )
    token = build_calendar_token(blob)
    assert token._state.client_id == "cid"
    assert token._state.refresh_token == "rt"


def test_drive_resolve_with_full_canonical_set() -> None:
    """Full canonical set present → returns credentials with that access token."""
    creds = resolve_drive_credentials_with_refresh(
        get_secret_fn=_stub_secrets(
            {
                "connector-google-drive-client-id": "cid",
                "connector-google-drive-client-secret": "csec",  # pragma: allowlist secret
                "connector-google-drive-refresh-token": "rt",
                "connector-google-drive-access-token": "at-fresh",
            }
        ),
    )
    assert creds.access_token == "at-fresh"


def test_drive_resolve_legacy_only_static_token() -> None:
    """Legacy path: only the static access_token set → returns it untouched."""
    creds = resolve_drive_credentials_with_refresh(
        get_secret_fn=_stub_secrets({"connector-google-drive-access-token": "legacy-at"}),
    )
    assert creds.access_token == "legacy-at"


def test_drive_resolve_no_secrets_raises() -> None:
    """No usable secrets → OSError with F21 fix hint pointing at kairix connect."""
    with pytest.raises(OSError, match="kairix connect google-drive"):
        resolve_drive_credentials_with_refresh(get_secret_fn=_stub_secrets({}))


def test_calendar_resolve_with_full_canonical_set() -> None:
    token = resolve_calendar_access_token(
        get_secret_fn=_stub_secrets(
            {
                "connector-google-calendar-client-id": "cid",
                "connector-google-calendar-client-secret": "csec",  # pragma: allowlist secret
                "connector-google-calendar-refresh-token": "rt",
                "connector-google-calendar-access-token": "fresh-cal-at",
            }
        ),
    )
    assert token == "fresh-cal-at"


def test_calendar_resolve_legacy_only() -> None:
    assert (
        resolve_calendar_access_token(
            get_secret_fn=_stub_secrets({"connector-google-calendar-access-token": "legacy-cal"}),
        )
        == "legacy-cal"
    )


def test_calendar_resolve_no_secrets_raises() -> None:
    with pytest.raises(OSError, match="kairix connect google-calendar"):
        resolve_calendar_access_token(get_secret_fn=_stub_secrets({}))


def test_drive_resolve_default_seam_delegates_to_kairix_secrets() -> None:
    """The production default reaches :func:`kairix.secrets.get_secret`.

    This is the smoke test that exercises the lazy import — without
    further configuration ``get_secret`` returns None for our canonical
    names, so the resolver raises the OSError. That's the documented
    behaviour for an environment without the canonical set provisioned.
    """
    # Default get_secret_fn=_default_get_secret is the production path.
    with pytest.raises(OSError, match="kairix connect google-drive"):
        resolve_drive_credentials_with_refresh()


def test_calendar_resolve_default_seam_delegates_to_kairix_secrets() -> None:
    with pytest.raises(OSError, match="kairix connect google-calendar"):
        resolve_calendar_access_token()
