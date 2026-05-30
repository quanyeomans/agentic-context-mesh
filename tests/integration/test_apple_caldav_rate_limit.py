"""Apple CalDAV throttling + auth-failure contract — F64.

Pins the CURRENT throttling contract for the
:class:`kairix.connectors.apple_caldav.AppleCalDavClient`. iCloud's
two documented backpressure / failure signals are:

* **503 Service Unavailable + Retry-After** — transient throttle. The
  client surfaces a :class:`requests.exceptions.HTTPError` (which the
  worker dead-letter path classifies as transient and re-tries on
  the next tick); no in-client retry loop today.
* **401 Unauthorized** — invalid app-specific password (or operator
  rotated the password without updating KV). Surfaces as a typed
  :class:`requests.exceptions.HTTPError` so the operator gets a loud
  signal that the credential needs rotating.

Tests substitute the entire :class:`AppleCalDavClient` via the
connector's ``client_factory`` constructor seam — no monkey-patching
:mod:`requests` or kairix internals (F1-clean / F2-clean).

Each ``test_*`` carries the ``@pytest.mark.integration`` marker per F8.
"""

from __future__ import annotations

import pytest
import requests

from kairix.connectors.apple_caldav import (
    AppleCalDavClient,
    CalDavCalendarRef,
    CalendarEventRecord,
    CalendarSyncPage,
)

pytestmark = pytest.mark.integration


class _ThrottlingClient(AppleCalDavClient):
    """Raises HTTP 503 + Retry-After on every list_changes call."""

    def __init__(self) -> None:
        # Skip the real __init__ — no auth, no caldav library import.
        self._username = "agent-alpha@example.com"
        self._password = "fixture-app-password"  # pragma: allowlist secret — test fixture
        self._endpoint = "https://caldav.icloud.com"
        self._dav_client_factory = None
        self._dav_client = None
        self.call_count = 0

    def discover_calendars(self) -> tuple[CalDavCalendarRef, ...]:
        return (
            CalDavCalendarRef(
                url="https://caldav.icloud.com/12345/calendars/personal/",
                display_name="Personal",
                ctag=None,
            ),
        )

    def list_changes(self, calendar_url: str, sync_token: str | None) -> CalendarSyncPage:
        del calendar_url, sync_token
        self.call_count += 1
        response = requests.Response()
        response.status_code = 503
        response.headers["Retry-After"] = "5"
        raise requests.exceptions.HTTPError("503 Service Unavailable", response=response)

    def fetch(self, event_url: str) -> CalendarEventRecord:
        raise NotImplementedError


class _UnauthorizedClient(AppleCalDavClient):
    """Raises HTTP 401 on every list_changes call — invalid app-password."""

    def __init__(self) -> None:
        self._username = "agent-alpha@example.com"
        self._password = "wrong-app-password"  # pragma: allowlist secret — test fixture
        self._endpoint = "https://caldav.icloud.com"
        self._dav_client_factory = None
        self._dav_client = None
        self.call_count = 0

    def discover_calendars(self) -> tuple[CalDavCalendarRef, ...]:
        return (
            CalDavCalendarRef(
                url="https://caldav.icloud.com/12345/calendars/personal/",
                display_name="Personal",
                ctag=None,
            ),
        )

    def list_changes(self, calendar_url: str, sync_token: str | None) -> CalendarSyncPage:
        del calendar_url, sync_token
        self.call_count += 1
        response = requests.Response()
        response.status_code = 401
        raise requests.exceptions.HTTPError("401 Unauthorized", response=response)

    def fetch(self, event_url: str) -> CalendarEventRecord:
        raise NotImplementedError


def _build_connector_with_client(client: AppleCalDavClient):
    """Construct the production connector against the supplied scripted client."""
    from kairix.connectors.apple_caldav import (
        AppleCalDavConfig,
        AppleCalDavConnector,
    )

    config = AppleCalDavConfig(
        username="agent-alpha@example.com",
        password="fixture-app-password",  # pragma: allowlist secret — test fixture
    )
    return AppleCalDavConnector(config, client_factory=lambda _c: client)


@pytest.mark.integration
def test_apple_caldav_503_with_retry_after_surfaces_typed_http_error() -> None:
    """503 + Retry-After surfaces as :class:`requests.exceptions.HTTPError`.

    The current contract is "no in-client retry on 503" — the typed
    error escapes ``list_changes`` directly so the worker's dead-letter
    path catches it. Future Retry-After honouring lands behind a flag.

    Sabotage proof: in
    :meth:`AppleCalDavConnector._drain_all_calendars`, wrap the
    ``client.list_changes(...)`` call in a ``try/except
    requests.exceptions.HTTPError`` that silently returns an empty
    batch. Re-run: the 503 body gets swallowed and the connector
    returns zero events instead of raising — every assertion that the
    connector raised fails. Restored.
    """
    client = _ThrottlingClient()
    connector = _build_connector_with_client(client)
    with pytest.raises(requests.exceptions.HTTPError) as exc_info:
        list(connector.list_changes(cursor=None))

    assert exc_info.value.response is not None
    assert exc_info.value.response.status_code == 503
    assert exc_info.value.response.headers.get("Retry-After") == "5"
    # Current contract: no in-client retry, the typed error surfaces on
    # the first 503 response. Pin one call to lock the no-retry behaviour.
    assert client.call_count == 1, (
        f"current contract: apple_caldav does not retry 503 (escapes to dead-letter); saw {client.call_count} calls"
    )


@pytest.mark.integration
def test_apple_caldav_401_unauthorized_surfaces_typed_http_error() -> None:
    """401 (invalid app-password) surfaces as :class:`requests.exceptions.HTTPError`.

    The 401 contract is "loud, no retry" — invalid app-passwords get
    surfaced to the operator immediately so they rotate the
    credential. This is distinct from 503 (throttle, transient) and
    403 (permission revocation, also loud).

    Sabotage proof: wrap the same call site in
    ``except requests.exceptions.HTTPError: pass`` — the 401 gets
    swallowed and the operator never finds out the password is wrong.
    Restored.
    """
    client = _UnauthorizedClient()
    connector = _build_connector_with_client(client)
    with pytest.raises(requests.exceptions.HTTPError) as exc_info:
        list(connector.list_changes(cursor=None))

    assert exc_info.value.response is not None
    assert exc_info.value.response.status_code == 401
    assert client.call_count == 1, (
        f"401 must not retry (operator needs to rotate app-password); saw {client.call_count} calls"
    )
