"""Step definitions for sharepoint_rate_limit.feature.

Drives the real :class:`kairix.connectors.sharepoint.SharePointConnector`
against an :class:`httpx.MockTransport`-backed Graph stub that returns
one 429 with ``Retry-After: 2`` before recovering on the next attempt.
The injected ``sleep_fn`` records every wait the retry loop requests
so the scenario can assert on the throttling budget without touching
wall clock time.

Per F46, this step file reaches the connector through the real
constructor + the production :class:`OAuth2ClientCredsAuth` helper
(depth ≤ 2). Direct construction is permitted in BDD step files when
the target is a Protocol-compliant leaf such as
:class:`SharePointConnector`.

F1-clean: no @patch / kairix module-attribute substitution.
F2-clean: no KAIRIX_* env-var manipulation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from pytest_bdd import given, parsers, then, when

from kairix.connectors.sharepoint import (
    SharePointConnector,
    SharePointCredentials,
    SharePointDriveSpec,
    SharePointGraphClient,
)
from kairix.core.protocols import ChangeEvent
from kairix.transport.auth.oauth2_client_creds import OAuth2ClientCredsAuth

pytestmark = pytest.mark.bdd

_DRIVE_ID = "b!drive-fixture-rate"
_DELTA_TOKEN_TAIL = "?$deltatoken=tok-rate"
_CURATED_PATH = "/Curated-Content"


def _one_pdf_envelope_page() -> dict[str, Any]:
    """One Graph drive-delta page with a single PDF under /Curated-Content."""
    return {
        "@odata.context": f"https://graph.microsoft.com/v1.0/$metadata#drives/{_DRIVE_ID}/root/delta",
        "value": [
            {
                "id": "01ITEMPDFRATELIMIT",
                "name": "team-playbook.pdf",
                "size": 12345,
                "lastModifiedDateTime": "2026-05-22T10:00:00Z",
                "webUrl": "https://contoso.sharepoint.com/sites/team/Documents/team-playbook.pdf",
                "file": {"mimeType": "application/pdf"},
                "parentReference": {
                    "driveId": _DRIVE_ID,
                    "path": f"/drives/{_DRIVE_ID}/root:{_CURATED_PATH}",
                },
            }
        ],
        "@odata.deltaLink": (f"https://graph.microsoft.com/v1.0/drives/{_DRIVE_ID}/root/delta{_DELTA_TOKEN_TAIL}"),
    }


@dataclass
class _Ctx:
    """Per-scenario context — no module-level mutable state."""

    requested_urls: list[str] = field(default_factory=list)
    recorded_sleeps: list[float] = field(default_factory=list)
    drive_call_count: int = 0
    connector: SharePointConnector | None = None
    events: list[ChangeEvent] = field(default_factory=list)


@pytest.fixture
def rate_limit_ctx() -> _Ctx:
    return _Ctx()


def _build_connector_with_throttled_then_ok_graph(ctx: _Ctx) -> SharePointConnector:
    """Construct the real connector wired to a stub that throttles once.

    First non-token GET returns ``429 + Retry-After: 2``; subsequent
    calls return the one-pdf delta page. The injected ``sleep_fn``
    records every wait so the scenario can assert the budget without
    delaying the suite.
    """

    def _stub_handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/oauth2/v2.0/token" in url:
            return httpx.Response(
                200,
                json={"access_token": "fake-bearer-rate-limit", "expires_in": 3600, "token_type": "Bearer"},
            )
        ctx.requested_urls.append(url)
        ctx.drive_call_count += 1
        if ctx.drive_call_count == 1:
            return httpx.Response(429, headers={"Retry-After": "2"}, json={"error": "throttled"})
        return httpx.Response(200, json=_one_pdf_envelope_page())

    transport = httpx.MockTransport(_stub_handler)
    shared_client = httpx.Client(transport=transport)

    auth = OAuth2ClientCredsAuth(
        tenant_id="fake-tenant",
        client_id="fake-client-id",
        client_secret="fake-client-secret-value",  # pragma: allowlist secret — test fixture
        scope="https://graph.microsoft.com/.default",
        http_client=shared_client,
    )

    def _client_builder(resolved_auth: OAuth2ClientCredsAuth) -> SharePointGraphClient:
        return SharePointGraphClient(
            auth=resolved_auth,
            http_client=shared_client,
            sleep_fn=ctx.recorded_sleeps.append,
        )

    return SharePointConnector(
        drives=[SharePointDriveSpec(drive_id=_DRIVE_ID)],
        credentials=SharePointCredentials(
            tenant_id="fake-tenant",
            client_id="fake-client-id",
            client_secret="fake-client-secret-value",  # pragma: allowlist secret — test fixture
        ),
        auth=auth,
        client_builder=_client_builder,
    )


# ---------------------------------------------------------------------------
# Givens
# ---------------------------------------------------------------------------


@given(
    parsers.parse(
        "a stubbed Microsoft Graph endpoint that returns 429 with Retry-After 2 once "
        "then 200 with a sample pdf envelope in /Curated-Content"
    )
)
def _given_throttle_once(rate_limit_ctx: _Ctx) -> None:
    rate_limit_ctx.connector = _build_connector_with_throttled_then_ok_graph(rate_limit_ctx)


# ---------------------------------------------------------------------------
# Whens
# ---------------------------------------------------------------------------


@when(parsers.parse("the operator runs the sharepoint connector list_changes against the throttled graph stub"))
def _when_list_changes(rate_limit_ctx: _Ctx) -> None:
    assert rate_limit_ctx.connector is not None, "Given step must run before When"
    rate_limit_ctx.events = list(rate_limit_ctx.connector.list_changes(cursor=None))


# ---------------------------------------------------------------------------
# Thens
# ---------------------------------------------------------------------------


@then("one created change event is emitted for the recovered drive")
def _one_created_event(rate_limit_ctx: _Ctx) -> None:
    events = rate_limit_ctx.events
    assert len(events) == 1, f"expected 1 event after retry, got {len(events)}: {events!r}"
    assert events[0].op == "created", f"expected created op, got {events[0]!r}"


@then("the throttling sleep budget recorded is 2 seconds")
def _sleep_budget_two_seconds(rate_limit_ctx: _Ctx) -> None:
    assert rate_limit_ctx.recorded_sleeps, (
        "expected at least one recorded sleep — the retry loop must call sleep_fn before retrying"
    )
    total = sum(rate_limit_ctx.recorded_sleeps)
    assert total == pytest.approx(2.0, abs=0.01), (
        f"throttling sleep budget must match Retry-After=2 seconds, total slept {total}s"
    )
